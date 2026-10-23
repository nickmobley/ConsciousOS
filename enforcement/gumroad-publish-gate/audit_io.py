"""Shared fail-closed audit writer for gate v1.2.3.

One lock protects validation and append. A damaged primary log is NEVER
modified or auto-repaired. Error incidents use a separate best-effort journal
and cannot authorize publication or serve as primary audit receipts.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone


def digest(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _read_checked(path):
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ValueError('audit log has truncated last line')
    prev = 'GENESIS'
    entries = []
    for line in raw.splitlines():
        if not line:
            raise ValueError('audit log has empty line')
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict) or entry.get('prev') != prev:
                raise ValueError('audit chain linkage invalid')
            h = entry.get('hash')
            if not isinstance(h, str) or len(h) != 64:
                raise ValueError('audit hash absent or invalid')
            unsigned = {k: v for k, v in entry.items() if k != 'hash'}
            if digest(unsigned) != h:
                raise ValueError('audit chain digest invalid')
            entries.append(entry)
            prev = h
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, OverflowError) as exc:
            raise ValueError('audit log has malformed entry') from exc
    return entries


class AuditUnavailable(Exception):
    pass


class AuditStore:
    def __init__(self, base):
        self.base = Path(base)
        self.path = self.base / 'publish-audit.jsonl'
        self.lock = self.base / 'publish-audit.lock'
        self.incident_path = self.base / 'publish-audit-failures.jsonl'
        self.incident_lock = self.base / 'publish-audit-failures.lock'

    def _lock(self, path):
        self.base.mkdir(parents=True, exist_ok=True)
        f = open(path, 'a+b')
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        return f

    def read_verified(self):
        try:
            with self._lock(self.lock):
                return _read_checked(self.path)
        except (OSError, ValueError) as e:
            raise AuditUnavailable(str(e)) from e

    def append(self, entry):
        try:
            with self._lock(self.lock):
                entries = _read_checked(self.path)
                event = dict(entry)
                event['prev'] = entries[-1]['hash'] if entries else 'GENESIS'
                event['hash'] = digest(event)
                with open(self.path, 'ab') as f:
                    f.write((json.dumps(event, allow_nan=False) + '\n').encode())
                    f.flush()
                    os.fsync(f.fileno())
                return event
        except (OSError, ValueError, TypeError, OverflowError) as e:
            raise AuditUnavailable(str(e)) from e

    def incident(self, source, item, error):
        # This is not an independently protected audit or security approval.
        # Never place package bytes, token contents, traceback secrets here.
        record = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'source': str(source), 'item': str(item), 'decision': 'DENY',
            'audit_integrity': 'UNVERIFIED', 'error_type': type(error).__name__,
        }
        try:
            with self._lock(self.incident_lock):
                with open(self.incident_path, 'ab') as f:
                    f.write((json.dumps(record) + '\n').encode())
                    f.flush()
                    os.fsync(f.fileno())
            return True
        except (OSError, ValueError):
            return False
