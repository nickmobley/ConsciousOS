#!/usr/bin/env python3
"""Gumroad publish gate — the policy enforcement point (Evolution Engine, 2026-10-23).
Every Gumroad publication attempt that goes through the fleet workflow calls this gate
BEFORE any write. The gate checks, in order:
  1. AUTHORIZATION: the queue item exists and carries an explicit authorization for a
     Gumroad publish, and its action class is not on the always-stopped list.
  2. DUPLICATE PROTECTION (two independent signals): (a) queue state — item not already
     COMPLETE with a live URL; (b) live storefront — no published product with the
     same name already on nickmobley.gumroad.com.
  3. SCOPE: requested price equals the price recorded for the item in the gate registry
     (no silent repricing); publishing only — this gate can never authorize a spend.
  4. AUDIT: every decision (ALLOW or DENY) is appended to a hash-chained JSONL audit log
     BEFORE the result is returned. A DENY stops the workflow before any external write.
On ALLOW the gate issues a permit file (item, product, price, package sha256, expiry 30 min).
The executor (separate step) must present a valid permit. Gate version 1.2 (fail-closed fixes; see PR).
Honest limit: permit/audit integrity is tamper-EVIDENT (hash chain), not tamper-proof
against an actor with full filesystem control; see the architecture doc for the route
inventory and the remaining bypass analysis.
Usage: gumroad_publish_gate.py --item CI-01(a) --name "<product name>" --price 24 --package <zip>
"""
import argparse, hashlib, json, pathlib, sys, time, urllib.request

BASE = pathlib.Path.home()/"workspace/operation-mobley/enforcement"
QUEUE = pathlib.Path.home()/"workspace/operation-mobley/commercial-deliverables-queue.md"
REGISTRY = BASE/"gate-registry.json"
AUDIT = BASE/"publish-audit.jsonl"
PERMITS = BASE/"permits"
STOPPED = {"spend","bid","contract","upwork-submit","job-application","new-channel-publish","credential-create"}

def audit(entry):
    prev = "GENESIS"
    if AUDIT.exists():
        lines = AUDIT.read_text().strip().splitlines()
        if lines: prev = json.loads(lines[-1])["hash"]
    entry["prev"] = prev
    entry["hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
    with AUDIT.open("a") as f: f.write(json.dumps(entry)+"\n")
    return entry["hash"]

STOREFRONT_URL = __import__("os").environ.get("GUMROAD_STOREFRONT_URL", "https://nickmobley.gumroad.com")

def storefront_names():
    try:
        req = urllib.request.Request(STOREFRONT_URL, headers={"User-Agent":"Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8","ignore")
        return html
    except Exception:
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True); ap.add_argument("--name", required=True)
    ap.add_argument("--price", type=float, required=True); ap.add_argument("--package", required=True)
    ap.add_argument("--action-class", default="gumroad-publish")
    a = ap.parse_args()
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    pkg = pathlib.Path(a.package)
    pkg_exists = pkg.exists()
    pkg_hash = hashlib.sha256(pkg.read_bytes()).hexdigest() if pkg_exists else "MISSING"
    reasons = []
    reg = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    item = reg.get(a.item)
    # 1 authorization
    if a.action_class in STOPPED: reasons.append(f"DENY authz: class {a.action_class} is always-stopped")
    if item is None: reasons.append("DENY authz: item not in gate registry (no recorded authorization envelope)")
    elif not item.get("authorized"): reasons.append("DENY authz: registry marks item not authorized")
    # 2a duplicate — queue state
    queue_ok = QUEUE.exists()
    q = QUEUE.read_text() if queue_ok else ""
    if item and item.get("queue_complete_marker") and item["queue_complete_marker"] in q:
        reasons.append("DENY duplicate: queue records this item COMPLETE with a live listing")
    # 2b duplicate — live storefront
    html = storefront_names()
    storefront_check = "UNKNOWN (fetch failed)"
    if html is None:
        reasons.append("DENY integrity: storefront check unavailable (fail-closed)")
    if html is not None:
        storefront_check = "MATCH" if a.name.split("—")[0].strip()[:24] in html else "no match"
        if storefront_check == "MATCH": reasons.append("DENY duplicate: a product with this name is already live on the storefront")
    # 3 scope
    if item and abs(item.get("price", -1) - a.price) > 1e-9:
        reasons.append(f"DENY scope: price {a.price} != registry envelope {item.get('price')}")
    if not pkg_exists: reasons.append("DENY integrity: package file missing (fail-closed)")
    if not queue_ok: reasons.append("DENY integrity: queue file unreadable (fail-closed)")
    decision = "DENY" if reasons else "ALLOW"
    entry = {"ts": now, "gate": "gumroad-publish v1.2", "item": a.item, "product": a.name,
             "price": a.price, "package_sha256": pkg_hash, "storefront_check": storefront_check,
             "decision": decision, "reasons": reasons or ["all checks passed"]}
    h = audit(entry)
    if decision == "ALLOW":
        PERMITS.mkdir(exist_ok=True)
        permit = {"item": a.item, "product": a.name, "price": a.price, "package_sha256": pkg_hash,
                  "issued": now, "expires_epoch": time.time()+1800, "audit_hash": h}
        (PERMITS/f"{a.item.replace('/','_')}.json").write_text(json.dumps(permit, indent=2))
        entry["permit"] = "issued"
    print(json.dumps(entry, indent=2))
    sys.exit(0 if decision == "ALLOW" else 3)

if __name__ == "__main__": main()
