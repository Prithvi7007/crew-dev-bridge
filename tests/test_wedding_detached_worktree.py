"""Wedding release host must recognize only the observed detached worktree.

These tests simulate filesystem metadata; no production Git, socket, or DB calls.
"""
from pathlib import Path

import pytest

from host import wedding_release_agent as release


@pytest.fixture
def checkout(monkeypatch, tmp_path):
    root = tmp_path / "wedding-v3-101f73e"
    root.mkdir()
    common = tmp_path / "wedding" / ".git"
    linked = common / "worktrees" / "wedding-v3-101f73e"
    linked.mkdir(parents=True)
    (root / ".git").write_text(f"gitdir: {linked}\n", encoding="utf-8")
    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.setattr(release, "GIT_COMMON_DIRECTORY", common)
    monkeypatch.setattr(release, "GIT_DIRECTORY", linked)
    values = {
        ("rev-parse", "--show-toplevel"): str(root),
        ("rev-parse", "--absolute-git-dir"): str(linked),
        ("rev-parse", "--git-common-dir"): str(common),
        ("rev-parse", "--abbrev-ref", "HEAD"): "HEAD",
        ("remote", "get-url", "origin"): release.REPO,
        ("status", "--porcelain"): "",
        ("rev-parse", "HEAD"): "a" * 40,
    }
    def fake_git(*args, **_kwargs):
        return values[args]
    monkeypatch.setattr(release, "git", fake_git)
    return root, common, linked, values


def test_exact_detached_linked_checkout_accepted(checkout):
    assert release.verify_detached_worktree() is None


def test_named_checkout_rejected(checkout):
    _, _, _, values = checkout
    values[("rev-parse", "--abbrev-ref", "HEAD")] = "main"
    with pytest.raises(release.Stop, match="expected_detached_head"):
        release.verify_detached_worktree()


def test_git_directory_instead_of_link_file_rejected(checkout):
    root, _, _, _ = checkout
    (root / ".git").unlink()
    (root / ".git").mkdir()
    with pytest.raises(release.Stop, match="invalid_checkout"):
        release.verify_detached_worktree()


def test_symlink_git_metadata_rejected(checkout):
    root, _, linked, _ = checkout
    (root / ".git").unlink()
    (root / ".git").symlink_to(linked, target_is_directory=True)
    with pytest.raises(release.Stop, match="invalid_checkout"):
        release.verify_detached_worktree()


def test_unrecognized_gitfile_path_rejected(checkout):
    root, _, _, _ = checkout
    (root / ".git").write_text("gitdir: /tmp/untrusted/.git\n", encoding="utf-8")
    with pytest.raises(release.Stop, match="unexpected_worktree_metadata"):
        release.verify_detached_worktree()


def test_git_reported_metadata_must_match(checkout):
    _, _, _, values = checkout
    values[("rev-parse", "--git-common-dir")] = "/tmp/wrong/common"
    with pytest.raises(release.Stop, match="unexpected_worktree_metadata"):
        release.verify_detached_worktree()


def test_missing_common_dir_rejected(checkout):
    _, common, _, _ = checkout
    # Do not remove the directory physically; imitate the unexpected metadata
    # by pinning a nonexistent shared location.
    release.GIT_COMMON_DIRECTORY = common / "not-existing"
    with pytest.raises(release.Stop, match="unexpected_worktree_metadata"):
        release.verify_detached_worktree()


def test_clean_detached_worktree_passes_preflight(monkeypatch, checkout, tmp_path):
    monkeypatch.setattr(release, "require_inactive_photo_uploads", lambda: None)
    sock = tmp_path / "wedding.sock"
    import socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(sock))
        monkeypatch.setattr(release, "APP_SOCKET", sock)
        backups = tmp_path / "backups"
        state = tmp_path / "state"
        backups.mkdir()
        state.mkdir()
        monkeypatch.setattr(release, "BACKUPS", backups)
        monkeypatch.setattr(release, "STATE", state)
        assert release.preflight() == "a" * 40


def test_unexpected_origin_fails_closed(monkeypatch, checkout):
    _, _, _, values = checkout
    values[("remote", "get-url", "origin")] = "https://example.com/other.git"
    with pytest.raises(release.Stop, match="unexpected_remote"):
        release.preflight()


def test_dirty_checkout_fails_closed(monkeypatch, checkout):
    _, _, _, values = checkout
    values[("status", "--porcelain")] = " M app/config.py"
    with pytest.raises(release.Stop, match="dirty_checkout"):
        release.preflight()


def test_release_service_grants_explicit_shared_metadata_write():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "deploy" / "wedding" / "wedding-release.service").read_text()
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/var/www/wedding-v3-101f73e /var/www/wedding/.git" in unit


def test_initial_workflow_change_is_operator_only(monkeypatch):
    def fake_git(*args, **_kwargs):
        return ".github/workflows/wedding-ci.yml" if "--name-only" in args else ""
    monkeypatch.setattr(release, "git", fake_git)
    with pytest.raises(release.Stop, match="manual_review"):
        release.allowed_changes("a" * 40, "b" * 40)
