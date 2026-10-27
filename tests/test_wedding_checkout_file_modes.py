"""Regression checks for Wedding V3's public CSS permission outage.

Git worktree checkout runs with a private agent umask (077), but files served by
Nginx must stay readable. These tests use only disposable tmp_path repositories.
"""
import os
from pathlib import Path
import stat
import subprocess

import pytest

from host import wedding_release_agent as release


def _git(repo: Path, *args):
    subprocess.run(["git", "-C", str(repo), *args],
                   check=True, capture_output=True)


def test_git_checkout_restores_public_read_permissions(tmp_path, monkeypatch):
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Offline Release Test")
    _git(repo, "config", "user.email", "release-test@example.invalid")
    asset = repo / "tokens.css"
    asset.write_text(":root { --cream: #fff0d5; }\n")
    _git(repo, "add", "tokens.css")
    _git(repo, "commit", "-qm", "Add CSS asset")
    asset.unlink()

    monkeypatch.setattr(release, "ROOT", repo)
    original_umask = os.umask(0o077)
    try:
        release.git("reset", "--hard", "HEAD")
    finally:
        os.umask(original_umask)

    assert asset.read_text().startswith(":root")
    assert stat.S_IMODE(asset.stat().st_mode) == 0o644


def test_umask_override_only_for_reset(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs.get("umask")))
        return ""

    monkeypatch.setattr(release, "run", fake_run)
    release.git("fetch", "--no-tags", "origin", "refs/heads/main")
    release.git("reset", "--hard", "a" * 40)
    release.git("diff", "--name-only", "a" * 40, "b" * 40)

    assert [entry[1] for entry in calls] == [None, 0o022, None]


def test_unreadable_css_blocks_release_before_service_restart(tmp_path, monkeypatch):
    root = tmp_path / "site"
    css = root / "app/static/css/tokens.css"
    css.parent.mkdir(parents=True)
    for directory in (root, root / "app", root / "app/static", css.parent):
        directory.chmod(0o755)

    css.write_text(":root { --cream: #fff0d5; }")
    css.chmod(0o600)
    monkeypatch.setattr(release, "ROOT", root)

    def fake_git(*args, **kwargs):
        assert args[:3] == ("diff", "--name-only", "--no-renames")
        return "app/static/css/tokens.css"

    monkeypatch.setattr(release, "git", fake_git)
    with pytest.raises(release.Stop, match="deployed_file_unreadable"):
        release.verify_changed_file_permissions("a" * 40, "b" * 40)

    css.chmod(0o644)
    assert release.verify_changed_file_permissions("a" * 40, "b" * 40) is None

    (root / "app/static").chmod(0o700)
    with pytest.raises(release.Stop, match="deployed_directory_unreadable"):
        release.verify_changed_file_permissions("a" * 40, "b" * 40)


def test_deleted_assets_do_not_block_permissions_check(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "git", lambda *a, **k: "app/static/css/removed.css")
    assert release.verify_changed_file_permissions("a" * 40, "b" * 40) is None
