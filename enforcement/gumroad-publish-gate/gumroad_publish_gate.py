#!/usr/bin/env python3
"""Gumroad publish gate — the policy enforcement point. Gate version 1.2.2.
Every Gumroad publication attempt in the fleet workflow calls this gate BEFORE any
write. Checks, in order:
  1. AUTHORIZATION: the item exists in the gate registry with `authorized` strictly
     boolean true, and its (normalized) action class is not on the always-stopped list.
  2. DUPLICATE PROTECTION (two signals): queue state AND a live storefront read
     (product names are HTML-unescaped before matching). FAIL-CLOSED: if the
     storefront cannot be read, the queue cannot be read, or the package is missing,
     the gate DENIES — an unavailable check never becomes an approval.
  3. SCOPE: the requested price must be a finite positive number and equal the
     registered envelope. (NaN defeats float comparison and is rejected outright.)
  4. AUDIT: every decision is appended to a hash-chained JSONL log under an exclusive
     file lock (concurrent appends cannot fork the chain), BEFORE the result returns.
On ALLOW the gate issues a 30-minute permit bound to the ALLOW audit entry's fields.
Any unexpected error is converted into an audited DENY — the gate never crashes
silently. Storefront URL is a code constant (no environment override).
Usage: gumroad_publish_gate.py --item X --name "<product>" --price N --package <zip>
"""
import argparse, fcntl, hashlib, html, json, math, pathlib, sys, time, unicodedata, urllib.request

BASE = pathlib.Path.home()/"workspace/operation-mobley/enforcement"
QUEUE = pathlib.Path.home()/"workspace/operation-mobley/commercial-deliverables-queue.md"
REGISTRY = BASE/"gate-registry.json"
AUDIT = BASE/"publish-audit.jsonl"
LOCK = BASE/"publish-audit.lock"
PERMITS = BASE/"permits"
STOREFRONT_URL = "https://nickmobley.gumroad.com"
STOPPED = {"spend","bid","contract","upwork-submit","job-application","new-channel-publish","credential-create"}

def norm_class(c):
    c = unicodedata.normalize("NFKC", str(c)).casefold()
    c = "".join(ch for ch in c if unicodedata.category(ch) not in ("Cf", "Cc"))
    return c.replace("_", "-").replace(" ", "-").strip()

def audit(entry):
    """Append one entry under an exclusive lock so concurrent writers cannot fork
    the hash chain: read-prev, compute, append — all inside the lock."""
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

def storefront_html():
    try:
        req = urllib.request.Request(STOREFRONT_URL, headers={"User-Agent": "Mozilla/5.0"})
        return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    except Exception:
        return None

def run(a):
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    reasons = []
    if not math.isfinite(a.price) or a.price <= 0:
        reasons.append(f"DENY scope: price {a.price} is not a finite positive number")
    if norm_class(a.action_class) in STOPPED:
        reasons.append(f"DENY authz: action class '{a.action_class}' is on the always-stopped list")
    pkg = pathlib.Path(a.package)
    pkg_exists = pkg.exists()
    pkg_hash = hashlib.sha256(pkg.read_bytes()).hexdigest() if pkg_exists else "MISSING"
    if not pkg_exists:
        reasons.append("DENY integrity: package file missing (fail-closed)")
    reg = {}
    if REGISTRY.exists():
        reg = json.loads(REGISTRY.read_text())
    item = reg.get(a.item)
    if item is None:
        reasons.append("DENY authz: item not in gate registry (no recorded authorization envelope)")
    elif item.get("authorized") is not True:
        reasons.append("DENY authz: registry does not mark this item authorized (strict boolean true required)")
    queue_ok = QUEUE.exists()
    q = QUEUE.read_text(errors="replace") if queue_ok else ""
    if not queue_ok:
        reasons.append("DENY integrity: queue file unreadable (fail-closed)")
    if item and item.get("queue_complete_marker") and item["queue_complete_marker"] in q:
        reasons.append("DENY duplicate: queue records this item COMPLETE with a live listing")
    raw = storefront_html()
    storefront_check = "UNAVAILABLE"
    if raw is None:
        reasons.append("DENY integrity: storefront check unavailable (fail-closed)")
    else:
        page = html.unescape(raw)
        needle = a.name.split("—")[0].strip()[:24]
        storefront_check = "MATCH" if (needle and needle in page) else "no match"
        if storefront_check == "MATCH":
            reasons.append("DENY duplicate: a product with this name is already live on the storefront")
    if item and not math.isfinite(item.get("price", float("nan"))):
        reasons.append("DENY scope: registry envelope price is not finite")
    if item and math.isfinite(a.price) and math.isfinite(item.get("price", float("nan"))) \
            and abs(item["price"] - a.price) > 1e-9:
        reasons.append(f"DENY scope: price {a.price} != registry envelope {item['price']}")
    decision = "DENY" if reasons else "ALLOW"
    entry = {"ts": now, "gate": "gumroad-publish v1.2.2", "item": a.item, "product": a.name,
             "price": a.price, "package_sha256": pkg_hash, "storefront_check": storefront_check,
             "decision": decision, "reasons": reasons or ["all checks passed"]}
    h = audit(entry)
    if decision == "ALLOW":
        PERMITS.mkdir(exist_ok=True)
        permit = {"item": a.item, "product": a.name, "price": a.price, "package_sha256": pkg_hash,
                  "issued": now, "expires_epoch": time.time() + 1800, "audit_hash": h}
        (PERMITS/f"{a.item.replace('/','_')}.json").write_text(json.dumps(permit, indent=2))
    print(json.dumps(entry, indent=2))
    return 0 if decision == "ALLOW" else 3

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--price", type=float, required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--action-class", default="gumroad-publish")
    a = ap.parse_args()
    try:
        sys.exit(run(a))
    except SystemExit:
        raise
    except Exception as e:  # never crash without a trace
        try:
            audit({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "gate": "gumroad-publish v1.2.2",
                   "item": a.item, "product": a.name, "decision": "DENY",
                   "reasons": [f"gate error converted to audited DENY: {type(e).__name__}: {e}"]})
        except Exception:
            pass
        print(json.dumps({"decision": "DENY", "error": type(e).__name__}))
        sys.exit(3)

if __name__ == "__main__":
    main()
