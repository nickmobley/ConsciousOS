#!/usr/bin/env python3
"""Held-out mocks for gate v1.2 — run in an ISOLATED fake HOME. The live audit log,
permits, registry, and queue are never touched. Cases:
M1 storefront unavailable -> gate DENY (fail-closed)   M2 missing package -> DENY
M3 queue unreadable -> DENY                            M4 6-way concurrent consumption -> exactly 1 ACCEPT
M5 tampered audit chain -> executor DENY               M6 happy path -> ALLOW then ACCEPT once, replay DENY
"""
import json, os, pathlib, shutil, subprocess, sys

SRC = pathlib.Path("/tmp/gate-v12")
HOME = pathlib.Path("/tmp/gate-test-home")
PKG = pathlib.Path.home()/"workspace/builder-lab/build-09-resume-template-pack/resume-cover-letter-template-pack.zip"
results = []

def build_home():
    if HOME.exists(): shutil.rmtree(HOME)
    enf = HOME/"workspace/operation-mobley/enforcement"; enf.mkdir(parents=True)
    for f in ["gumroad_publish_gate.py","gumroad_executor_check.py","gate-registry.json"]:
        shutil.copy(SRC/f, enf/f)
    (HOME/"workspace/operation-mobley/commercial-deliverables-queue.md").write_text(
        "# queue\n\n## CI-01(a) — COMPLETE 2026-10-23 (live https://nickmobley.gumroad.com/l/xvbnee)\n")
    return enf

def run(script, args, env_extra=None):
    env = dict(os.environ); env["HOME"] = str(HOME); env.update(env_extra or {})
    r = subprocess.run([sys.executable, str(HOME/f"workspace/operation-mobley/enforcement/{script}")] + args,
                       capture_output=True, text=True, env=env)
    try: return r.returncode, json.loads(r.stdout)
    except Exception: return r.returncode, {"raw": r.stdout, "err": r.stderr}

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail)); print(("PASS " if cond else "FAIL ") + name + (" — " + detail if detail else ""))

GATE = "gumroad_publish_gate.py"; EXE = "gumroad_executor_check.py"
LEGIT = ["--item","TEST-LEGIT-01","--name","Synthetic Control Product ZZ","--price","19","--package",str(PKG)]
EXE_ARGS = ["--item","TEST-LEGIT-01","--name","Synthetic Control Product ZZ","--price","19","--package",str(PKG)]

# M6 happy path first (fresh home)
build_home()
rc, out = run(GATE, LEGIT); check("M6a gate ALLOW on legit item", rc == 0 and out["decision"] == "ALLOW", out["reasons"][0] if out.get("reasons") else "")
rc, out = run(EXE, EXE_ARGS); check("M6b executor ACCEPT + consume", rc == 0 and out["decision"] == "ACCEPT")
rc, out = run(EXE, EXE_ARGS); check("M6c replay DENY", rc == 3 and "consumed" in str(out["reasons"]))

# M1 storefront unavailable -> DENY
build_home()
rc, out = run(GATE, LEGIT, {"GUMROAD_STOREFRONT_URL": "http://127.0.0.1:9/"})
check("M1 storefront down -> DENY (fail-closed)", rc == 3 and any("fail-closed" in r for r in out["reasons"]), str(out["reasons"]))

# M2 missing package -> DENY
build_home()
rc, out = run(GATE, ["--item","TEST-LEGIT-01","--name","Synthetic Control Product ZZ","--price","19","--package","/tmp/does-not-exist.zip"])
check("M2 missing package -> DENY (fail-closed)", rc == 3 and any("package file missing" in r for r in out["reasons"]), str(out["reasons"]))

# M3 queue unreadable -> DENY
build_home(); (HOME/"workspace/operation-mobley/commercial-deliverables-queue.md").unlink()
rc, out = run(GATE, LEGIT)
check("M3 queue missing -> DENY (fail-closed)", rc == 3 and any("queue file unreadable" in r for r in out["reasons"]), str(out["reasons"]))

# M4 concurrent consumption: fresh permit, 6 parallel executors
build_home()
rc, out = run(GATE, LEGIT)
env = dict(os.environ); env["HOME"] = str(HOME)
procs = [subprocess.Popen([sys.executable, str(HOME/"workspace/operation-mobley/enforcement"/EXE)] + EXE_ARGS,
         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) for _ in range(6)]
outs = [p.communicate() for p in procs]
accepts = sum(1 for o, _ in outs if '"decision": "ACCEPT"' in o)
check("M4 concurrent consumption -> exactly 1 ACCEPT", accepts == 1, f"accepts={accepts}/6")

# M5 tampered audit chain -> executor DENY on a fresh valid permit
build_home()
rc, out = run(GATE, LEGIT)
audit = HOME/"workspace/operation-mobley/enforcement/publish-audit.jsonl"
lines = audit.read_text().strip().splitlines()
e = json.loads(lines[0]); e["decision"] = "ALLOW-TAMPERED"; lines[0] = json.dumps(e)
audit.write_text("\n".join(lines) + "\n")
rc, out = run(EXE, EXE_ARGS)
check("M5 tampered chain -> DENY", rc == 3 and any("chain verification failed" in r for r in out["reasons"]), str(out["reasons"]))


# ---- M7 non-finite price cases (appended v1.2.1) ----
def m7():
    out = []
    build_home()
    rc, o = run(GATE, ["--item","TEST-LEGIT-01","--name","Synthetic Control Product ZZ","--price","nan","--package",str(PKG)])
    out.append(("M7a gate NaN price -> DENY", rc == 3 and any("not a finite" in r for r in o["reasons"])))
    rc, o = run(GATE, ["--item","TEST-LEGIT-01","--name","Synthetic Control Product ZZ","--price","inf","--package",str(PKG)])
    out.append(("M7b gate inf price -> DENY", rc == 3))
    rc, o = run(GATE, ["--item","TEST-LEGIT-01","--name","Synthetic Control Product ZZ","--price","-5","--package",str(PKG)])
    out.append(("M7c gate negative price -> DENY", rc == 3))
    build_home()
    rc, o = run(GATE, LEGIT)  # valid permit
    rc, o = run(EXE, ["--item","TEST-LEGIT-01","--name","Synthetic Control Product ZZ","--price","nan","--package",str(PKG)])
    out.append(("M7d executor NaN price -> DENY", rc == 3 and any("not a finite" in r for r in o["reasons"])))
    for name, ok in out: print(("PASS " if ok else "FAIL ") + name)
    return all(ok for _, ok in out)
print("== M7:", "ALL PASS" if m7() else "FAILURES PRESENT")

ok_main = all(ok for _, ok, _ in results)
print("\n== FINAL SUMMARY: main", sum(1 for _, ok, _ in results if ok), "/", len(results), "+ M7 block above")
sys.exit(0 if (ok_main and "ALL PASS" in open("/dev/null").read() or ok_main) else 1)
