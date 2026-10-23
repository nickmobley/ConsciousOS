"""Offline integration tests for v1.2.3 candidate; no real account access.

All subprocesses are pinned to generated fixture data. A sitecustomize hook
blocks TCP/UDP socket connections. No test uses a real product ZIP.
"""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

SRC = Path(__file__).resolve().parent


class GateCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='candidate-', dir=SRC)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.enf = self.home / 'workspace/operation-mobley/enforcement'
        self.enf.mkdir(parents=True)
        self.store = self.root / 'store.html'
        self.store.write_text('<html><body>Other unrelated product</body></html>')
        self.package = self.root / 'fixture.zip'
        with zipfile.ZipFile(self.package, 'w') as f: f.writestr('test.txt', 'test-only')
        self.item = 'DEV-123'
        self.product = 'Fixture Dry Run Sample'
        self.action = ['--item', self.item, '--name', self.product, '--price', '19', '--package', str(self.package)]
        self.env = os.environ.copy()
        self.env['HOME'] = str(self.home)
        self.env['PYTHONPATH'] = str(self.root)
        (self.root / 'sitecustomize.py').write_text('''import socket
# Any attempted network connection is an immediate test failure.
def _deny(*a, **k): raise RuntimeError("network forbidden in acceptance tests")
socket.socket.connect = _deny
socket.create_connection = _deny
''')
        self.setup_registry()
        self.reset_gate_scripts()

    def tearDown(self):
        self.tmp.cleanup()

    def setup_registry(self, item='DEV-123', price=19, authorized=True):
        (self.enf / 'gate-registry.json').write_text(json.dumps({item: {
            'authorized': authorized, 'price': price, 'queue_complete_marker': f'{item} — COMPLETE',
        }}))
        q = self.home / 'workspace/operation-mobley/commercial-deliverables-queue.md'
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text('# synthetic queue with no completed product\n')

    def reset_gate_scripts(self):
        for p in ['audit_io.py', 'gumroad_publish_gate.py', 'gumroad_executor_check.py']:
            text = (SRC / p).read_text()
            if p == 'gumroad_publish_gate.py':
                anchor = "STOREFRONT_URL = 'https://nickmobley.gumroad.com'"
                self.assertIn(anchor, text, 'fixture replacement anchor changed')
                text = text.replace(anchor, f'STOREFRONT_URL = {self.store.as_uri()!r}')
            (self.enf / p).write_text(text)

    def run_script(self, which='gumroad_publish_gate.py', args=None):
        return subprocess.run([sys.executable, str(self.enf / which), *(args if args is not None else self.action)],
                              env=self.env, capture_output=True, text=True, timeout=12)

    def call(self, which='gumroad_publish_gate.py', args=None):
        p = self.run_script(which, args)
        self.assertNotIn('Traceback', p.stderr)
        self.assertIn(p.returncode, (0, 3), p.stdout + '\n' + p.stderr)
        try: payload = json.loads(p.stdout)
        except ValueError: self.fail(f'not JSON: {p.stdout} stderr={p.stderr}')
        return p.returncode, payload

    def audit_lines(self):
        p = self.enf / 'publish-audit.jsonl'
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def verify(self):
        spec = importlib.util.spec_from_file_location('audit_io_test', self.enf / 'audit_io.py')
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m.AuditStore(self.enf).read_verified()

    def test_happy_path_and_replay(self):
        rc, p = self.call(); self.assertEqual((rc, p['decision']), (0, 'ALLOW'))
        rc, p = self.call('gumroad_executor_check.py'); self.assertEqual((rc, p['decision']), (0, 'ACCEPT'))
        rc, p = self.call('gumroad_executor_check.py'); self.assertEqual(rc, 3); self.assertIn('replay', str(p['reasons']))
        self.assertEqual(len(self.verify()), 3)

    def test_malformed_and_nonfinite_prices_gate(self):
        for value in ['nan','inf','-inf','0','-5','text','']:
            with self.subTest(value=value):
                args = self.action.copy(); args[args.index('--price')+1] = value
                if value.startswith('-inf'):
                    args[args.index('--price')] = '--price=' + value
                    del args[args.index('--price=' + value)+1]
                rc, out = self.call(args=args)
                self.assertEqual(rc, 3)
                self.assertIn('finite positive', str(out['reasons']))
                self.assertEqual(self.audit_lines()[-1]['decision'], 'DENY')

    def test_malformed_and_nonfinite_prices_executor(self):
        self.call()
        for value in ['nan','inf','-inf','0','-5','text','']:
            with self.subTest(value=value):
                args = self.action.copy(); args[args.index('--price')+1] = value
                if value.startswith('-inf'):
                    args[args.index('--price')] = '--price=' + value
                    del args[args.index('--price=' + value)+1]
                rc, out = self.call('gumroad_executor_check.py',args)
                self.assertEqual(rc, 3)
                self.assertIn('finite positive', str(out['reasons']))
                self.assertEqual(self.audit_lines()[-1]['decision'], 'DENY')

    def test_missing_package_gate_and_executor(self):
        args = self.action.copy(); args[-1] = str(self.root / 'missing.zip')
        rc, p = self.call(args=args); self.assertEqual(rc, 3); self.assertIn('package', str(p['reasons']))
        self.call()
        rc, p = self.call('gumroad_executor_check.py', args);self.assertEqual(rc, 3);self.assertIn('package', str(p['reasons']))
        self.assertEqual(self.audit_lines()[-1]['decision'], 'DENY')

    def test_storefront_fail_closed(self):
        self.store.unlink()
        rc,p = self.call();self.assertEqual(rc,3);self.assertIn('storefront',str(p['reasons']))

    def test_queue_fail_closed(self):
        (self.home / 'workspace/operation-mobley/commercial-deliverables-queue.md').unlink()
        rc,p = self.call();self.assertEqual(rc,3);self.assertIn('queue',str(p['reasons']))

    def test_storefront_html_escaped_duplicate(self):
        self.store.write_text('<html><body>Fish &amp; Chips Training Pack</body></html>')
        args=self.action.copy();args[args.index('--name')+1]='Fish & Chips Training Pack'
        rc,p=self.call(args=args);self.assertEqual(rc,3);self.assertIn('duplicate',str(p['reasons']))

    def test_action_only_explicitly_allowed(self):
        for action in ['Spend', 'SPEND', 'purchase', 'bi\u200bd', 'gumroad_publ ish', '', 'gumroad-publish '] :
            with self.subTest(action=action):
                rc,p=self.call(args=self.action+['--action-class',action]);self.assertEqual(rc,3);self.assertIn('action class',str(p['reasons']))
        rc,p=self.call();self.assertEqual(rc,0)

    def test_registry_authorization_requires_bool(self):
        for bad in ['false', 1, None, False]:
            with self.subTest(bad=bad):
                self.setup_registry(authorized=bad)
                rc,p=self.call();self.assertEqual(rc,3);self.assertIn('strict boolean',str(p['reasons']))

    def test_registry_invalid_price(self):
        for bad in ['inf','nan','text',0,None]:
            with self.subTest(bad=bad):
                self.setup_registry(price=bad)
                rc,p=self.call();self.assertEqual(rc,3);self.assertIn('registry envelope',str(p['reasons']))

    def test_test_only_item_blocked(self):
        self.setup_registry(item='TEST-LEGIT-01')
        args=self.action.copy();args[args.index('--item')+1]='TEST-LEGIT-01'
        rc,p=self.call(args=args);self.assertEqual(rc,3);self.assertIn('test-only',str(p['reasons']))

    def test_denied_audit_hash_cannot_be_reused(self):
        args=self.action.copy();args[args.index('--price')+1]='99'
        rc,denied=self.call(args=args);self.assertEqual(rc,3)
        self.call()
        permit_path=self.enf / 'permits/DEV-123.json'
        permit=json.loads(permit_path.read_text());permit['audit_hash']=self.audit_lines()[0]['hash'];permit_path.write_text(json.dumps(permit))
        rc,p=self.call('gumroad_executor_check.py');self.assertEqual(rc,3);self.assertIn('not ALLOW',str(p['reasons']))

    def test_wrong_permit_item_rejected(self):
        self.call()
        path=self.enf/'permits/DEV-123.json';permit=json.loads(path.read_text());permit['item']='OTHER';path.write_text(json.dumps(permit))
        rc,p=self.call('gumroad_executor_check.py');self.assertEqual(rc,3);self.assertIn('item mismatch',str(p['reasons']))

    def test_corrupt_primary_unmodified_and_incident_recorded(self):
        self.call()
        ap=self.enf/'publish-audit.jsonl'
        with open(ap,'ab') as f:f.write(b'{invalid\n')
        before=ap.read_bytes()
        rc,p=self.call('gumroad_executor_check.py')
        self.assertEqual(rc,3);self.assertEqual(ap.read_bytes(),before)
        incident=self.enf/'publish-audit-failures.jsonl'
        self.assertTrue(incident.exists())
        self.assertIn('UNVERIFIED',incident.read_text())
        rc,p=self.call();self.assertEqual(rc,3);self.assertEqual(ap.read_bytes(),before)

    def test_concurrent_gate_audit_chain(self):
        reg={f'BURST-{i}':{'authorized':True,'price':i+10,'queue_complete_marker':f'BURST-{i} COMPLETE'} for i in range(8)}
        (self.enf/'gate-registry.json').write_text(json.dumps(reg))
        commands=[]
        for i in range(8):
            args=['--item',f'BURST-{i}','--name',f'Burst Product {i}','--price',str(i+10),'--package',str(self.package)]
            commands.append(args)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outs=list(pool.map(lambda x:self.run_script(args=x),commands))
        self.assertEqual([p.returncode for p in outs],[0]*8,[p.stderr for p in outs])
        self.assertEqual(len(self.verify()),8)

    def test_concurrent_executor_single_accept_and_valid_audit(self):
        self.call()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outs=list(pool.map(lambda _: self.run_script('gumroad_executor_check.py'),range(8)))
        self.assertEqual(sum(p.returncode==0 for p in outs),1,[(p.returncode,p.stdout,p.stderr) for p in outs])
        self.assertEqual(len(self.verify()),9)

    def test_permit_invalid_expiry_fails_closed(self):
        self.call()
        path=self.enf/'permits/DEV-123.json';permit=json.loads(path.read_text());permit['expires_epoch']='never';path.write_text(json.dumps(permit))
        rc,p=self.call('gumroad_executor_check.py');self.assertEqual(rc,3);self.assertIn('expiry',str(p['reasons']))

    def test_corrupted_consumed_registry_fails_closed(self):
        self.call()
        (self.enf/'consumed-permits.jsonl').write_text('{garbled}\n')
        rc,p=self.call('gumroad_executor_check.py');self.assertEqual(rc,3);self.assertIn('consumed',str(p['reasons']))

    def test_existing_log_can_be_verified_and_accepted(self):
        self.call()
        valid=self.verify()
        self.assertEqual(len(valid),1)
        self.assertEqual(valid[0]['decision'],'ALLOW')


if __name__=='__main__':
    unittest.main(verbosity=2)
