#!/usr/bin/env python3
"""Executor-side permit validation (gate v1.2.2) — the controlled tool's front door.
Before ANY publish write, the executor must present a gate permit. This validator:
  - loads the permit and matches item/product/price/package sha256 against the
    execution request (tamper check);
  - BINDS the permit to its audit entry: the entry named by the permit's audit hash
    must exist, be an ALLOW, and carry identical item/product/price/package fields
    (a permit reusing a DENIED entry's hash, or issued for another item, is rejected);
  - verifies the FULL audit hash chain (recomputed, not membership-only); any
    unparseable line fails verification closed;
  - enforces the 30-minute expiry and SINGLE USE (atomic O_EXCL consumption claim);
  - audits its own decision either way. ANY unexpected error (missing package,
    truncated log, non-numeric permit price) becomes an audited DENY — the executor
    never crashes without a trace.
Execution remains a DRY RUN in v1.2.2 (no platform write) — the validator is the
mechanical boundary; the write channel plugs in behind it after owner decisions.
Usage: gumroad_executor_check.py --item X --name "<name>" --price N --package <zip>
"""
import argparse, fcntl, hashlib, json, math, os, pathlib, sys, time

BASE = pathlib.Path.home()/"workspace/operation-mobley/enforcement"
AUDIT = BASE/"publish-audit.jsonl"
LOCK = BASE/"publish-audit.lock"
PERMITS = BASE/"permits"
CONSUMED = BASE/"consumed-permits.jsonl"

def audit(entry):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        prev = "GENESIS"
        if AUDIT.exists():
            lines = AUDIT.read_text(errors="replace").strip().splitlines()
            if lines:
                try:
                    prev = json.loads(lines[-1])["hash"]
                except Exception:
                    prev = "UNPARSEABLE-TAIL"
                    entry["tail_warning"] = "previous audit line unparseable; chain already broken"
        entry["prev"] = prev
        entry["hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
        with AUDIT.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    return entry["hash"]

def load_audit():
    """Return (entries, ok). ok=False if any line is unparseable — fail closed."""
    entries = []
    if not AUDIT.exists():
        return entries, True
    with open(LOCK, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_SH)
        text = AUDIT.read_text(errors="replace")
    for line in text.strip().splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            return entries, False
    return entries, True

def verify_chain(entries):
    prev = "GENESIS"
    for e in entries:
        if e.get("prev") != prev:
            return False
        h = e["hash"]
        e2 = dict(e)
        del e2["hash"]
        if hashlib.sha256(json.dumps(e2, sort_keys=True).encode()).hexdigest() != h:
            return False
        prev = h
    return True

def try_consume(permit_hash, item, now):
    """Atomically claim the permit: exactly one concurrent caller can create the
    per-permit lock file; everyone else gets False."""
    lock = BASE/f"consumed-{permit_hash}.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"audit_hash": permit_hash, "item": item, "consumed_at": now}))
    with CONSUMED.open("a") as f:
        f.write(json.dumps({"audit_hash": permit_hash, "item": item, "consumed_at": now}) + "\n")
    return True

def run(a):
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    reasons = []
    if not math.isfinite(a.price) or a.price <= 0:
        reasons.append(f"DENY permit: requested price {a.price} is not a finite positive number")
    permit = None
    pf = PERMITS/f"{a.item.replace('/','_')}.json"
    if not pf.exists():
        reasons.append("DENY permit: none issued for this item")
    else:
        permit = json.loads(pf.read_text())
    entries, audit_ok = load_audit()
    if not audit_ok:
        reasons.append("DENY permit: audit log contains an unparseable line (fail-closed)")
    elif not verify_chain(entries):
        reasons.append("DENY permit: audit chain verification failed (tamper or corruption)")
    if permit is not None:
        pkg_hash = hashlib.sha256(pathlib.Path(a.package).read_bytes()).hexdigest()
        if permit.get("product") != a.name:
            reasons.append("DENY permit: product mismatch (tamper or wrong permit)")
        p_price = permit.get("price")
        if not isinstance(p_price, (int, float)) or isinstance(p_price, bool) or not math.isfinite(p_price):
            reasons.append("DENY permit: permit price is not a finite number")
        elif math.isfinite(a.price) and abs(p_price - a.price) > 1e-9:
            reasons.append("DENY permit: price mismatch vs permit envelope")
        if permit.get("package_sha256") != pkg_hash:
            reasons.append("DENY permit: package hash mismatch")
        if isinstance(permit.get("expires_epoch"), (int, float)) and time.time() > permit["expires_epoch"]:
            reasons.append("DENY permit: expired")
        # Binding: the permit must name an ALLOW audit entry with identical fields.
        bound = next((e for e in entries if e.get("hash") == permit.get("audit_hash")), None)
        if bound is None:
            reasons.append("DENY permit: audit hash not found in audit log")
        else:
            if bound.get("decision") != "ALLOW":
                reasons.append("DENY permit: bound audit entry is not an ALLOW")
            if bound.get("item") != permit.get("item") or bound.get("item") != a.item:
                reasons.append("DENY permit: item mismatch vs bound audit entry")
            if bound.get("product") != permit.get("product"):
                reasons.append("DENY permit: product mismatch vs bound audit entry")
            if bound.get("price") != permit.get("price"):
                reasons.append("DENY permit: price mismatch vs bound audit entry")
            if bound.get("package_sha256") != permit.get("package_sha256"):
                reasons.append("DENY permit: package mismatch vs bound audit entry")
        consumed = set()
        if CONSUMED.exists():
            for line in CONSUMED.read_text(errors="replace").strip().splitlines():
                try:
                    consumed.add(json.loads(line)["audit_hash"])
                except Exception:
                    pass
        if permit.get("audit_hash") in consumed:
            reasons.append("DENY permit: already consumed (replay)")
    decision = "DENY" if reasons else "ACCEPT"
    entry = {"ts": now, "gate": "executor-check v1.2.2", "item": a.item, "product": a.name,
             "decision": decision,
             "reasons": reasons or ["permit valid; consumed atomically; dry-run boundary passed (no platform write)"]}
    if decision == "ACCEPT":
        if not try_consume(permit["audit_hash"], a.item, now):
            decision = "DENY"
            entry["decision"] = "DENY"
            entry["reasons"] = ["DENY permit: consumption race lost — another executor claimed this permit first"]
    audit(entry)
    print(json.dumps(entry, indent=2))
    return 0 if decision == "ACCEPT" else 3

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--price", type=float, required=True)
    ap.add_argument("--package", required=True)
    a = ap.parse_args()
    try:
        sys.exit(run(a))
    except SystemExit:
        raise
    except Exception as e:  # missing package, bad permit JSON, anything: audited DENY
        try:
            audit({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "gate": "executor-check v1.2.2",
                   "item": a.item, "product": a.name, "decision": "DENY",
                   "reasons": [f"executor error converted to audited DENY: {type(e).__name__}: {e}"]})
        except Exception:
            pass
        print(json.dumps({"decision": "DENY", "error": type(e).__name__}))
        sys.exit(3)

if __name__ == "__main__":
    main()
