#!/usr/bin/env python3
"""Controlled-route executor validation v1.2.3: DRY RUN, no platform write.

Rejects unbound, expired, malformed and previously consumed permits; enforces
append-only audit validation and best-effort incident record on corruption.
"""
import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import time
from audit_io import AuditStore, AuditUnavailable

BASE = pathlib.Path.home() / 'workspace/operation-mobley/enforcement'
PERMITS = BASE / 'permits'
CONSUMED = BASE / 'consumed-permits.jsonl'
AUDIT = AuditStore(BASE)


def price_of(raw):
    try:
        n = float(raw)
        if not math.isfinite(n) or n <= 0:
            raise ValueError('not positive finite')
        return n
    except (ValueError, TypeError, OverflowError) as e:
        raise ValueError('price is not a finite positive number') from e


def audit_deny(item, product, reasons):
    event = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'gate': 'executor-check v1.2.3',
        'item': item, 'product': product, 'decision': 'DENY', 'reasons': reasons,
    }
    try:
        AUDIT.append(event)
    except Exception as e:
        AUDIT.incident('executor', item, e)
    return event


def try_consume(permit_hash, item, now):
    if not re.fullmatch(r'[0-9a-f]{64}', permit_hash):
        raise ValueError('invalid permit hash')
    claim = BASE / ('consumed-' + permit_hash + '.lock')
    fd = None
    try:
        fd = os.open(str(claim), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, 'w') as f:
        f.write(json.dumps({'audit_hash': permit_hash, 'item': item, 'consumed_at': now}))
        f.flush()
        os.fsync(f.fileno())
    with open(CONSUMED, 'a') as f:
        f.write(json.dumps({'audit_hash': permit_hash, 'item': item, 'consumed_at': now}) + '\n')
        f.flush()
        os.fsync(f.fileno())
    return True


def validate(a):
    reasons = []
    try:
        requested_price = price_of(a.price)
    except ValueError as e:
        requested_price = None
        reasons.append('DENY permit: ' + str(e))
    if a.item.startswith('TEST-'):
        reasons.append('DENY permit: test-only item cannot execute')
    permit = None
    try:
        permit = json.loads((PERMITS / (a.item.replace('/', '_') + '.json')).read_text())
        if not isinstance(permit, dict):
            raise ValueError('permit not object')
    except (OSError, ValueError, TypeError):
        reasons.append('DENY permit: permit missing or malformed')

    try:
        entries = AUDIT.read_verified()
    except AuditUnavailable as e:
        # Do not rewrite a damaged primary audit log. Fail closed, separate journal.
        AUDIT.incident('executor-audit-corruption', a.item, e)
        return {'decision': 'DENY', 'reasons': ['DENY integrity: primary audit unavailable or corrupt'], 'audit_integrity': 'UNVERIFIED'}, 3

    if permit is not None:
        try:
            pkg_hash = hashlib.sha256(pathlib.Path(a.package).read_bytes()).hexdigest()
        except (OSError, ValueError):
            pkg_hash = None
            reasons.append('DENY permit: package missing or unreadable')
        try:
            permit_price = price_of(permit.get('price'))
        except ValueError:
            permit_price = None
            reasons.append('DENY permit: permit price is not a finite positive number')
        if permit.get('item') != a.item:
            reasons.append('DENY permit: item mismatch')
        if permit.get('product') != a.name:
            reasons.append('DENY permit: product mismatch')
        if requested_price is not None and permit_price is not None and abs(requested_price - permit_price) > 1e-9:
            reasons.append('DENY permit: price mismatch')
        if pkg_hash != permit.get('package_sha256'):
            reasons.append('DENY permit: package hash mismatch')
        expiry = permit.get('expires_epoch')
        if not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not math.isfinite(expiry) or time.time() > expiry:
            reasons.append('DENY permit: expired or invalid expiry')
        bound = next((e for e in entries if e.get('hash') == permit.get('audit_hash')), None)
        if bound is None:
            reasons.append('DENY permit: matching audit entry missing')
        else:
            if bound.get('decision') != 'ALLOW':
                reasons.append('DENY permit: bound audit entry is not ALLOW')
            for field, expected in [('item', a.item), ('product', a.name), ('package_sha256', pkg_hash)]:
                if bound.get(field) != expected or permit.get(field) != expected:
                    reasons.append('DENY permit: bound audit ' + field + ' mismatch')
            if requested_price is None or not isinstance(bound.get('price'), (int,float)) or isinstance(bound.get('price'),bool) or not math.isfinite(bound['price']) or abs(bound['price']-requested_price)>1e-9:
                reasons.append('DENY permit: bound audit price mismatch')
        if CONSUMED.exists():
            try:
                for line in CONSUMED.read_text().splitlines():
                    if json.loads(line).get('audit_hash') == permit.get('audit_hash'):
                        reasons.append('DENY permit: already consumed (replay)')
                        break
            except (OSError, ValueError):
                reasons.append('DENY integrity: consumed registry unreadable')

    if reasons:
        result = audit_deny(a.item, a.name, reasons)
        return result, 3
    now = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    try:
        success = try_consume(permit['audit_hash'], a.item, now)
    except Exception as exc:
        result = audit_deny(a.item, a.name, ['DENY permit: consumption failed: ' + type(exc).__name__])
        return result, 3
    if not success:
        result = audit_deny(a.item, a.name, ['DENY permit: already consumed (replay)'])
        return result, 3
    entry = {
        'ts': now, 'gate': 'executor-check v1.2.3', 'item': a.item,
        'product': a.name, 'decision': 'ACCEPT',
        'reasons': ['DRY RUN only; no platform write'],
    }
    try:
        AUDIT.append(entry)
    except Exception as exc:
        AUDIT.incident('executor-postconsume', a.item, exc)
        return {'decision': 'DENY', 'reasons': ['DENY integrity: accept audit failed'], 'audit_integrity': 'UNVERIFIED'}, 3
    return entry, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--item', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--price', required=True)  # deliberate: parse within audited execution
    ap.add_argument('--package', required=True)
    # argparse mistakes '-inf' for another option unless we bind it to price.
    argv = sys.argv[1:]
    for i in range(len(argv) - 1):
        if argv[i] == '--price' and argv[i + 1].startswith('-') and argv[i + 1] not in ('--item', '--name', '--package', '--action-class', '--price'):
            argv[i] = '--price=' + argv.pop(i + 1)
            break
    a = ap.parse_args(argv)
    try:
        result, code = validate(a)
    except Exception as exc:
        result = audit_deny(a.item, a.name, ['DENY internal error: ' + type(exc).__name__])
        code = 3
    print(json.dumps(result, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
