"""Run all local hermetic unittest methods in bounded subprocess batches.
Avoid long single-process silence; aggregate exit codes and preserve raw traces.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess, sys, unittest
HERE = Path(__file__).resolve().parent
import test_gate_candidate
names = unittest.defaultTestLoader.getTestCaseNames(test_gate_candidate.GateCases)
groups = [names[i::4] for i in range(4)]

def run(entry):
    index, names = entry
    command = [sys.executable, '-m', 'unittest', '-v'] + ['test_gate_candidate.GateCases.'+n for n in names]
    p = subprocess.run(command, cwd=HERE, capture_output=True, text=True, timeout=135)
    log = HERE / f'acceptance-batch-{index}.log'
    log.write_text('COMMAND: '+' '.join(command)+'\nEXIT: '+str(p.returncode)+'\n\n'+p.stdout+p.stderr)
    return (index,len(names),p.returncode,log)

with ThreadPoolExecutor(max_workers=4) as pool:
    results = sorted(pool.map(run,enumerate(groups,1)))
for i,n,code,log in results:
    print(f'BATCH {i}: {n} method(s), exit={code}, log={log.name}')
    tail=log.read_text().splitlines()[-5:]
    print('\n'.join(tail))
print(f'OVERALL: {sum(n for _,n,_,_ in results)} methods, '+('ALL PASS' if all(c==0 for _,_,c,_ in results) else 'FAILURES'))
sys.exit(0 if all(c==0 for _,_,c,_ in results) else 1)
