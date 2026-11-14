"""Fingerprinting retains exact-path artifact checks without a blanket source cap."""
from pathlib import Path
import pytest
from bridge import core

@pytest.mark.parametrize('relative_path,byte_count,permitted', [
    ('app/static/react/player.js',329_760,True),
    ('app/static/react/player.js',450_001,False),
    ('app/static/react/player.css',249_000,True),
    ('app/static/react/player.css',250_001,False),
    ('frontend/src/player/SeasonRewind.jsx',250_001,True),
    ('frontend/package-lock.json',500_000,True),
])
def test_fingerprint_verified_assets_only(tmp_path,monkeypatch,relative_path,byte_count,permitted):
    path=tmp_path/'asset'
    path.write_bytes(b'a'*byte_count)
    monkeypatch.setattr(core,'changed_paths',lambda:[relative_path])
    monkeypatch.setattr(core,'safe_path',lambda _path:path)
    monkeypatch.setattr(core,'branch',lambda:'feature/fingerprint-test')
    if permitted:
        assert len(core.fingerprint()) == 64
    else:
        with pytest.raises(core.BridgeError,match='Oversized verified build artifact'):
            core.fingerprint()

def test_fingerprint_uses_only_verified_asset_budget():
    source=(Path(__file__).resolve().parents[1]/'bridge/core.py').read_text()
    body=source.split('def fingerprint()',1)[1].split('def status()',1)[0]
    assert 'enforce_verified_artifact_limit(p, target.stat().st_size)' in body
    assert 'MAX_FILE' not in body
