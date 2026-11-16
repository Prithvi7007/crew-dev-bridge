import subprocess
import pytest
from bridge import core
from runner import worker

def test_bolt_isolation(tmp_path,monkeypatch):
    p=tmp_path/'bolt';p.mkdir()
    for args in [('init','-q','-b','feature/test-bolt'),('config','user.email','test@example.invalid'),('config','user.name','Test')]:
        subprocess.run(['git','-C',str(p),*args],check=True,capture_output=True)
    (p/'README.md').write_text('ok')
    subprocess.run(['git','-C',str(p),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(p),'commit','-qm','initial'],check=True,capture_output=True)
    for n,v in [('BOLT_WORKSPACE',p),('QUEUE',tmp_path/'queue'),('LOCK',tmp_path/'queue'/'lock'),('AUDIT',tmp_path/'audit'/'log')]:monkeypatch.setattr(core,n,v)
    with core.repository_context('bolt-ai'):
        assert core._repo()=='Prithvi7007/Bolt_ai'
        assert core.status()['production_access'] is False
        with pytest.raises(core.BridgeError):core.start_check('career_test')
        assert core.start_check('bolt_api_tests')['kind']=='bolt_api_tests'
    assert core._repo()==core.REPO

def test_bolt_checks_allowlisted():
    assert {'bolt_api_tests','bolt_desktop_syntax'} <= worker.ALLOWED
