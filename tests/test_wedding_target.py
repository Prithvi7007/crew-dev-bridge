"""Regression tests for the third, independently-scoped development target."""
from pathlib import Path

import pytest

from bridge import core


def test_wedding_repo_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'WEDDING_WORKSPACE', tmp_path / 'wedding-source')
    with core.repository_context('wedding'):
        assert core._repo() == 'Prithvi7007/Wedding_Website_Final'
        assert core._workspace() == tmp_path / 'wedding-source'
        assert core._base_branch() == 'v3-production'
    with core.repository_context('crew_games'):
        assert core._base_branch() == 'main'
    with core.repository_context('crew-dev-bridge'):
        assert core._base_branch() == 'main'
    with pytest.raises(core.BridgeError):
        with core.repository_context('arbitrary-repo'):
            pass


def test_wedding_credential_is_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'TOKEN_FILE', tmp_path / 'crew_token')
    monkeypatch.setattr(core, 'WEDDING_TOKEN_FILE', tmp_path / 'wedding_token')
    with core.repository_context('wedding'):
        with pytest.raises(core.BridgeError, match='credential not provisioned'):
            core.github_request('GET', 'git/ref/heads/v3-production')
