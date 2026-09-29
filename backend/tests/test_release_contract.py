from pathlib import Path
import json

from app.main import app, UploadAdmissionMiddleware

ROOT=Path(__file__).resolve().parents[2]


def test_public_deployment_contract():
    nginx=(ROOT/'deploy'/'nginx.conf').read_text()
    api=(ROOT/'frontend'/'src'/'api.ts').read_text()
    assert 'client_max_body_size 21m;' in nginx
    assert 'limit_conn_zone' in nginx and 'limit_req_zone' in nginx
    assert 'limit_conn farseat_conn' in nginx and 'limit_req zone=farseat_rate' in nginx
    assert "VITE_API_BASE ?? ''" in api


def test_frontend_lockfile_is_committed_and_version_pinned():
    package=json.loads((ROOT/'frontend'/'package.json').read_text())
    lock=json.loads((ROOT/'frontend'/'package-lock.json').read_text())
    assert lock['packages']['']['version']==package['version']=='1.0.3'
    for section in ('dependencies','devDependencies'):
        for _name,version in package.get(section,{}).items():
            assert version and not any(ch in version for ch in '^~*xX')


def test_upload_admission_middleware_is_installed_before_route_handling():
    # FastAPI records middleware wrappers before building the ASGI stack. This guards
    # the specific architectural requirement that upload admission wraps multipart
    # parsing instead of starting inside the endpoint after UploadFile already exists.
    assert any(m.cls is UploadAdmissionMiddleware for m in app.user_middleware)


def test_browser_gate_uses_playwright_managed_chromium_by_default():
    script=(ROOT/'scripts'/'browser_e2e.py').read_text()
    assert "FARSEAT_CHROMIUM_EXECUTABLE" in script
    assert "executable_path='/usr/bin/chromium'" not in script


def test_release_validator_rejects_browser_evidence_from_another_commit(tmp_path):
    import importlib.util
    validator_path=ROOT/'scripts'/'validate_release.py'
    spec=importlib.util.spec_from_file_location('farseat_validate_release_test', validator_path)
    assert spec and spec.loader
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPORT_DIR=tmp_path
    (tmp_path/'browser-e2e.json').write_text(json.dumps({
        'status':'PASS',
        'source_commit':'old-commit',
        'source_dirty':False,
    }))
    result=module.browser_gate('new-commit', False)
    assert result['status']=='FAIL'
    assert 'commit mismatch' in result['reason']
