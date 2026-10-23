#!/usr/bin/env python3
"""Gumroad controlled-route publication policy gate v1.2.3 (DRY-RUN ONLY).

A gate permit is only a local candidate, not owner identity or production
permission. No external publication occurs here. Independent verifier and
credential isolation are not yet in force.
"""
import argparse
import hashlib
import html
import json
import math
import pathlib
import sys
import time
import urllib.request
from audit_io import AuditStore, AuditUnavailable

BASE = pathlib.Path.home() / 'workspace/operation-mobley/enforcement'
QUEUE = pathlib.Path.home() / 'workspace/operation-mobley/commercial-deliverables-queue.md'
REGISTRY = BASE / 'gate-registry.json'
PERMITS = BASE / 'permits'
STOREFRONT_URL = 'https://nickmobley.gumroad.com'
AUDIT = AuditStore(BASE)


def price_of(raw):
    try:
        n = float(raw)
        if not math.isfinite(n) or n <= 0:
            raise ValueError('not positive finite')
        return n
    except (ValueError, TypeError, OverflowError) as e:
        raise ValueError('price is not a finite positive number') from e


def storefront_html():
    req = urllib.request.Request(STOREFRONT_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=20) as response:
        if getattr(response, 'status', None) not in (None, 200):
            raise ValueError('storefront HTTP response not OK')
        return response.read().decode('utf-8', errors='replace')


def evaluate(a):
    reasons = []
    requested_price = None
    try:
        requested_price = price_of(a.price)
    except ValueError as e:
        reasons.append('DENY scope: ' + str(e))

    # No denylist or normalizable synonyms: only one action is admitted.
    # A malicious unknown action cannot gain publication authority.
    if a.action_class != 'gumroad-publish':
        reasons.append('DENY authz: action class not explicitly permitted')
    # Fixture identifiers must never be publishable in the controlled route.
    if a.item.startswith('TEST-'):
        reasons.append('DENY authz: test-only item cannot be published')

    pkg_hash = 'MISSING'
    try:
        pkg_hash = hashlib.sha256(pathlib.Path(a.package).read_bytes()).hexdigest()
    except (OSError, ValueError):
        reasons.append('DENY integrity: package file missing or unreadable')

    item = None
    try:
        registry = json.loads(REGISTRY.read_text())
        if not isinstance(registry, dict):
            raise ValueError('registry is not a mapping')
        item = registry.get(a.item)
        if not isinstance(item, dict) or item.get('authorized') is not True:
            reasons.append('DENY authz: registry requires strict boolean authorization')
        else:
            try:
                registered_price = price_of(item.get('price'))
                if requested_price is not None and abs(registered_price - requested_price) > 1e-9:
                    reasons.append('DENY scope: price differs from registry envelope')
            except ValueError:
                reasons.append('DENY scope: registry envelope price is invalid')
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        reasons.append('DENY integrity: registry unavailable or malformed')

    try:
        queue = QUEUE.read_text()
        if item and item.get('queue_complete_marker'):
            if item['queue_complete_marker'] in queue:
                reasons.append('DENY duplicate: queue records item COMPLETE')
    except (OSError, UnicodeDecodeError):
        reasons.append('DENY integrity: queue unavailable')

    storefront_check = 'UNAVAILABLE'
    try:
        page = html.unescape(storefront_html())
        needle = a.name.split('—')[0].strip()[:24]
        if not needle or needle in page:
            storefront_check = 'MATCH'
            reasons.append('DENY duplicate: product already live or invalid name')
        else:
            storefront_check = 'NO-MATCH-IN-PAGE (not an authoritative catalog)'
    except Exception:
        reasons.append('DENY integrity: storefront check unavailable')

    decision = 'DENY' if reasons else 'ALLOW'
    event = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'gate': 'gumroad-publish v1.2.3',
        'item': a.item, 'product': a.name, 'price': requested_price,
        'package_sha256': pkg_hash, 'storefront_check': storefront_check,
        'decision': decision, 'reasons': reasons or ['all checks passed (dry-run only)'],
    }
    saved = AUDIT.append(event)
    if decision == 'ALLOW':
        # Permit is only usable by a dry-run validator; no Gumroad write integration.
        PERMITS.mkdir(parents=True, exist_ok=True)
        permit = {
            'item': a.item, 'product': a.name, 'price': requested_price,
            'package_sha256': pkg_hash, 'issued': event['ts'],
            'expires_epoch': time.time() + 1800, 'audit_hash': saved['hash'],
        }
        # Atomic replacement of a fully-written permit; still NOT key-isolated.
        tmp = PERMITS / (a.item.replace('/', '_') + '.json.tmp')
        dest = PERMITS / (a.item.replace('/', '_') + '.json')
        tmp.write_text(json.dumps(permit, indent=2))
        tmp.replace(dest)
    return event


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--item', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--price', required=True)  # string: invalid input remains audit-able
    ap.add_argument('--package', required=True)
    ap.add_argument('--action-class', default='gumroad-publish')
    # argparse mistakes '-inf' for another option unless we bind it to price.
    argv = sys.argv[1:]
    for i in range(len(argv) - 1):
        if argv[i] == '--price' and argv[i + 1].startswith('-') and argv[i + 1] not in ('--item', '--name', '--package', '--action-class', '--price'):
            argv[i] = '--price=' + argv.pop(i + 1)
            break
    a = ap.parse_args(argv)
    try:
        result = evaluate(a)
        print(json.dumps(result, indent=2))
        return 0 if result['decision'] == 'ALLOW' else 3
    except Exception as exc:
        try:
            AUDIT.append({
                'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'gate': 'gumroad-publish v1.2.3', 'item': a.item,
                'product': a.name, 'decision': 'DENY',
                'reasons': ['DENY internal error: ' + type(exc).__name__],
            })
        except Exception as audit_exc:
            AUDIT.incident('gate', a.item, audit_exc)
        print(json.dumps({'decision': 'DENY', 'reason': 'gate failure; no permit'}))
        return 3


if __name__ == '__main__':
    sys.exit(main())
