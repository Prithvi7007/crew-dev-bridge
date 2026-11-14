"""Verified CREW build artifacts retain bounded contracts; ordinary sources do not."""
from pathlib import Path
import pytest
from bridge import core
from runner import worker
ROOT = Path(__file__).resolve().parents[1]

def test_only_verified_compiled_assets_have_size_budgets():
    assert core.FRONTEND_ARTIFACT_LIMITS == {
        'app/static/react/player.js': 450_000,
        'app/static/react/player.css': 250_000,
    }
    assert core.FRONTEND_ARTIFACT_LIMITS == worker.FRONTEND_ARTIFACT_MAX
    for source in ('app/static/js/ui.js','frontend/package-lock.json','frontend/src/player/SeasonRewind.jsx','not-player.js'):
        core.enforce_verified_artifact_limit(source,500_000)
    for path,limit in core.FRONTEND_ARTIFACT_LIMITS.items():
        core.enforce_verified_artifact_limit(path,limit)
        with pytest.raises(core.BridgeError,match='Oversized verified build artifact'):
            core.enforce_verified_artifact_limit(path,limit+1)

def test_sandbox_and_verified_artifact_policy_remain_in_place():
    src=(ROOT/'bridge/core.py').read_text(encoding='utf-8')
    runner=(ROOT/'runner/worker.py').read_text(encoding='utf-8')
    compose=(ROOT/'compose.yaml').read_text(encoding='utf-8')
    assert 'st.st_size > FRONTEND_ARTIFACT_LIMITS[rel]' in src
    assert 'hashlib.sha256(raw).hexdigest() != manifest[rel]' in src
    assert 'enforce_verified_artifact_limit(path, target.stat().st_size)' in src
    assert 'enforce_verified_artifact_limit(p, target.stat().st_size)' in src
    assert 'st.st_size > FRONTEND_ARTIFACT_MAX[rel]' in runner
    assert 'len(raw) > FRONTEND_ARTIFACT_MAX[rel]' in runner
    assert "raw.decode('utf-8')" in runner
    assert 'network_mode: none' in compose
    assert 'read_only: true' in compose
    assert 'preexec_fn=_drop_test_privileges' in runner
