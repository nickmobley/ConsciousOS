# Gumroad Publish Gate — Mobley Fleet enforcement point (v1.1)
The fleet's first mechanically executed commercial boundary: no Gumroad publication
happens without passing this gate. Built 2026-10-23 (Musa); independent security review
assigned to Cipher.

Components:
- `gumroad_publish_gate.py` — pre-publish checks: authorization envelope (gate-registry),
  duplicate protection via queue state AND live storefront read, scope (price must equal
  the registered envelope; spend classes always-stopped), hash-chained audit log written
  before any result; ALLOW issues a 30-minute permit.
- `gumroad_executor_check.py` — executor-side validator: permit must match the exact
  execution request (product/price/package SHA-256), audit hash must exist in the chain,
  expiry enforced, permits are SINGLE-USE (consumed registry = replay protection).
- `gate-registry.json` — authorization envelopes per item.

Test evidence (2026-10-23, from the live audit log): duplicate publish DENIED on both
signals; unregistered item DENIED; legitimate control ALLOWED + permit; tampered price
DENIED; valid permit ACCEPTED + consumed; replay DENIED; forged expired permit DENIED.
Audit chain: hash-linked JSONL, verified valid across all entries.

Known limits (stated, not hidden): enforcement governs the controlled route; the shared
browser session remains an alternate route until the executor channel is verified and
Nick approves sign-out (sequencing is his). Audit integrity is tamper-EVIDENT (hash
chain), not tamper-proof against an actor with full filesystem control. The operational
audit log lives on the Musa VM and is exchanged via the fleet Drive bridge; this repo
copy is the shared, versioned source for the code.
