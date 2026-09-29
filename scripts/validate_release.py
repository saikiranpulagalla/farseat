#!/usr/bin/env python3
from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORT_DIR=ROOT/'validation'/'reports'
REQUIRED=[
    ROOT/'backend'/'app'/'geometry.py', ROOT/'backend'/'app'/'reference.py', ROOT/'backend'/'app'/'numeric.py',
    ROOT/'backend'/'app'/'parser.py', ROOT/'backend'/'app'/'analysis.py', ROOT/'backend'/'app'/'store.py',
    ROOT/'backend'/'tests'/'test_api_integration.py', ROOT/'backend'/'tests'/'test_factorization.py',
    ROOT/'backend'/'tests'/'test_parser.py', ROOT/'backend'/'tests'/'test_store.py', ROOT/'backend'/'tests'/'test_reference.py',
    ROOT/'frontend'/'package-lock.json', ROOT/'frontend'/'src'/'App.tsx', ROOT/'frontend'/'src'/'requestCoordinator.ts',
    ROOT/'frontend'/'src'/'requestCoordinator.test.ts', ROOT/'frontend'/'src'/'components'/'PdfInspector.tsx',
    ROOT/'sample'/'farseat-demo.pdf', ROOT/'scripts'/'browser_e2e.py',
    ROOT/'validation'/'reference'/'PUBLIC_AVIXA_BDM_REFERENCE_V1.json',
    ROOT/'validation'/'defect-ledger.json', ROOT/'validation'/'RELEASE_GATE.md'
]


def run(name,cmd,cwd):
    p=subprocess.run(cmd,cwd=cwd,text=True,capture_output=True)
    return {'name':name,'command':' '.join(cmd),'exit_code':p.returncode,'status':'PASS' if p.returncode==0 else 'FAIL','stdout':p.stdout[-12000:],'stderr':p.stderr[-12000:]}


def git_identity():
    try:
        commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True,check=True).stdout.strip()
        dirty=bool(subprocess.run(['git','status','--porcelain'],cwd=ROOT,text=True,capture_output=True,check=True).stdout.strip())
        return commit,dirty
    except Exception:
        return None,None


def evidence_exists(spec: str) -> bool:
    if not spec:
        return False
    path_text, sep, symbol = spec.partition('::')
    path=ROOT/path_text
    if not path.exists():
        return False
    if not sep:
        return True
    try:
        return symbol in path.read_text(errors='ignore')
    except Exception:
        return False


def defect_gate():
    ledger=json.loads((ROOT/'validation'/'defect-ledger.json').read_text())
    blockers=[]
    stale=[]
    for defect in ledger.get('defects',[]):
        if defect.get('severity') not in {'P0','P1'}:
            continue
        if defect.get('status')!='CLOSED':
            blockers.append(defect)
            continue
        evidence=defect.get('regression_test','')
        if not evidence_exists(evidence):
            stale.append({'id':defect.get('id'),'regression_test':evidence})
    ok=not blockers and not stale
    return {'name':'defect-ledger','exit_code':0 if ok else 1,'status':'PASS' if ok else 'FAIL','open_blockers':blockers,'stale_regression_evidence':stale}


def browser_gate(expected_commit, expected_dirty):
    path=REPORT_DIR/'browser-e2e.json'
    if not path.exists():
        return {'name':'browser-e2e','exit_code':None,'status':'UNVERIFIED','reason':'browser-e2e.json missing; run scripts/browser_e2e.py against live backend/frontend'}
    try:
        data=json.loads(path.read_text())
    except Exception:
        return {'name':'browser-e2e','exit_code':1,'status':'FAIL','reason':'browser-e2e.json is invalid'}
    if expected_commit is None:
        return {'name':'browser-e2e','exit_code':None,'status':'UNVERIFIED','reason':'current source commit unavailable; browser evidence cannot be bound to source','report':data}
    if expected_dirty:
        return {'name':'browser-e2e','exit_code':1,'status':'FAIL','reason':'working tree is dirty; browser evidence cannot validate modified source','report':data}
    report_commit=data.get('source_commit')
    if report_commit != expected_commit:
        return {'name':'browser-e2e','exit_code':1,'status':'FAIL','reason':f'browser evidence commit mismatch: report={report_commit!r} current={expected_commit!r}','report':data}
    if data.get('source_dirty') is not False:
        return {'name':'browser-e2e','exit_code':1,'status':'FAIL','reason':'browser evidence was generated from a dirty or unidentified tree','report':data}
    status='PASS' if data.get('status')=='PASS' else 'FAIL'
    return {'name':'browser-e2e','exit_code':0 if status=='PASS' else 1,'status':status,'report':data}


def main():
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    missing=[str(p.relative_to(ROOT)) for p in REQUIRED if not p.exists()]
    suites=[{'name':'required-files','exit_code':0 if not missing else 1,'status':'PASS' if not missing else 'FAIL','missing':missing}]
    suites.append(defect_gate())
    suites.append(run('python-compile',[sys.executable,'-m','compileall','-q','app','tests'],ROOT/'backend'))
    suites.append(run('backend-tests',[sys.executable,'-m','pytest','-q'],ROOT/'backend'))
    suites.append(run('production-api-gate',[sys.executable,'-m','pytest','-q','tests/test_api_integration.py','tests/test_factorization.py','tests/test_parser.py','tests/test_reference.py','tests/test_geometry.py','tests/test_store.py'],ROOT/'backend'))

    node_modules=ROOT/'frontend'/'node_modules'
    if node_modules.exists() and (node_modules/'.bin'/'vitest').exists():
        # Windows installs npm as a .cmd shim, which CreateProcess does not
        # resolve when invoked directly from Python.
        npm = 'npm.cmd' if sys.platform == 'win32' else 'npm'
        suites.append(run('frontend-tests',[npm,'test','--','--run'],ROOT/'frontend'))
        suites.append(run('frontend-build',[npm,'run','build'],ROOT/'frontend'))
    else:
        suites.append({'name':'frontend-tests','exit_code':None,'status':'UNVERIFIED','reason':'complete node_modules absent; CI must run npm ci from pinned lockfile'})
        suites.append({'name':'frontend-build','exit_code':None,'status':'UNVERIFIED','reason':'complete node_modules absent; CI must run npm ci from pinned lockfile'})

    commit,dirty=git_identity()
    suites.append(browser_gate(commit,dirty))

    if commit is None:
        suites.append({'name':'source-identity','exit_code':None,'status':'UNVERIFIED','reason':'git commit identity unavailable'})
    elif dirty:
        suites.append({'name':'source-identity','exit_code':1,'status':'FAIL','reason':'working tree is dirty; validation must match an exact commit'})
    else:
        suites.append({'name':'source-identity','exit_code':0,'status':'PASS','source_commit':commit})
    overall='PASS' if all(s['status']=='PASS' for s in suites) else ('FAIL' if any(s['status']=='FAIL' for s in suites) else 'UNVERIFIED')
    report={
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'platform':platform.platform(),
        'python':sys.version,
        'contract':'EC-1.2',
        'release_candidate':'1.0.3',
        'source_commit':commit,
        'source_dirty':dirty,
        'overall':overall,
        'suites':suites,
    }
    path=REPORT_DIR/'release-validation.json';path.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    return 0 if overall=='PASS' else 1

if __name__=='__main__': raise SystemExit(main())
