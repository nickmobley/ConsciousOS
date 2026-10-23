#!/usr/bin/env python3
"""Executor-side permit validation (gate v1.1) — the controlled tool's front door.
Before ANY publish write, the executor must present a gate permit. This validator:
  - loads the permit for the item and matches item/product/price/package sha256 against
    the execution request (tamper check);
  - confirms the permit's audit hash exists in the hash-chained publish audit log;
  - enforces the 30-minute expiry;
  - enforces SINGLE USE: a consumed-permit registry makes replay fail;
  - appends its own decision to the audit log either way.
In v1.1 the execution itself is a DRY RUN (no platform write) — the validator is the
mechanical boundary being tested; the write channel (API token or dedicated profile)
plugs in behind it once Nick's channel step is done.
Usage: gumroad_executor_check.py --item X --name "<name>" --price N --package <zip>
"""
import argparse, hashlib, json, pathlib, sys, time

BASE = pathlib.Path.home()/"workspace/operation-mobley/enforcement"
AUDIT = BASE/"publish-audit.jsonl"; PERMITS = BASE/"permits"; CONSUMED = BASE/"consumed-permits.jsonl"

def audit(entry):
    prev = "GENESIS"
    if AUDIT.exists():
        lines = AUDIT.read_text().strip().splitlines()
        if lines: prev = json.loads(lines[-1])["hash"]
    entry["prev"] = prev
    entry["hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
    with AUDIT.open("a") as f: f.write(json.dumps(entry)+"\n")
    return entry["hash"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True); ap.add_argument("--name", required=True)
    ap.add_argument("--price", type=float, required=True); ap.add_argument("--package", required=True)
    a = ap.parse_args()
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    reasons = []
    pf = PERMITS/f"{a.item.replace('/','_')}.json"
    permit = None
    if not pf.exists(): reasons.append("DENY permit: none issued for this item")
    else:
        permit = json.loads(pf.read_text())
        pkg_hash = hashlib.sha256(pathlib.Path(a.package).read_bytes()).hexdigest()
        if permit["product"] != a.name: reasons.append("DENY permit: product mismatch (tamper or wrong permit)")
        if abs(permit["price"] - a.price) > 1e-9: reasons.append("DENY permit: price mismatch vs permit envelope")
        if permit["package_sha256"] != pkg_hash: reasons.append("DENY permit: package hash mismatch")
        if time.time() > permit["expires_epoch"]: reasons.append("DENY permit: expired")
        hashes = {json.loads(l)["hash"] for l in AUDIT.read_text().strip().splitlines()} if AUDIT.exists() else set()
        if permit["audit_hash"] not in hashes: reasons.append("DENY permit: audit hash not found in audit log")
        consumed = set()
        if CONSUMED.exists():
            consumed = {json.loads(l)["audit_hash"] for l in CONSUMED.read_text().strip().splitlines()}
        if permit["audit_hash"] in consumed: reasons.append("DENY permit: already consumed (replay)")
    decision = "DENY" if reasons else "ACCEPT"
    entry = {"ts": now, "gate": "executor-check v1.1", "item": a.item, "product": a.name,
             "decision": decision, "reasons": reasons or ["permit valid; consumed; dry-run execution boundary passed (no platform write in v1.1)"]}
    audit(entry)
    if decision == "ACCEPT":
        with CONSUMED.open("a") as f:
            f.write(json.dumps({"audit_hash": permit["audit_hash"], "item": a.item, "consumed_at": now})+"\n")
    print(json.dumps(entry, indent=2)); sys.exit(0 if decision == "ACCEPT" else 3)

if __name__ == "__main__": main()
