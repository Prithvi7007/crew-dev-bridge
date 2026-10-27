"""The Motion-enabled player build may be larger; all other guardrails stay bounded."""
from pathlib import Path

from bridge import core
from runner import worker

ROOT = Path(__file__).resolve().parents[1]


def test_only_verified_player_bundle_gets_compiled_artifact_budget():
    assert core.MAX_FILE == 250_000
    assert core.MAX_PLAYER_BUNDLE == 450_000
    assert core.max_file_size('app/static/react/player.js') == 450_000
    assert core.max_file_size('app/static/react/player.css') == 250_000
    assert core.max_file_size('app/static/js/ui.js') == 250_000
    assert core.max_file_size('frontend/package-lock.json') == 250_000
    assert core.max_file_size('not-player.js') == 250_000
    assert worker.FRONTEND_ARTIFACT_MAX == {
        'app/static/react/player.js': 450_000,
        'app/static/react/player.css': 250_000,
    }


def test_sandbox_and_verified_artifact_policy_remain_in_place():
    src = (ROOT / 'bridge/core.py').read_text(encoding='utf-8')
    runner = (ROOT / 'runner/worker.py').read_text(encoding='utf-8')
    compose = (ROOT / 'compose.yaml').read_text(encoding='utf-8')
    assert 'st.st_size > max_file_size(rel)' in src
    assert "hashlib.sha256(raw).hexdigest() != manifest[rel]" in src
    assert 'target.stat().st_size > max_file_size(path)' in src
    assert 'st.st_size > FRONTEND_ARTIFACT_MAX[rel]' in runner
    assert 'len(raw) > FRONTEND_ARTIFACT_MAX[rel]' in runner
    assert 'raw.decode(\'utf-8\')' in runner
    assert 'network_mode: none' in compose
    assert 'read_only: true' in compose
    assert 'preexec_fn=_drop_test_privileges' in runner
