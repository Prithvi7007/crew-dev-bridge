#!/usr/bin/env python3
"""Prepare the wedding *development* target in a clean crew-dev-bridge checkout.

This does NOT provision credentials, touch either production app/database, deploy,
or push commits. Inspect git diff and run the repository tests before committing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def require_replace(s: str, old: str, new: str, label: str) -> str:
    hits = s.count(old)
    if hits != 1:
        raise RuntimeError(f"{label}: expected exactly one matching anchor; got {hits}")
    return s.replace(old, new, 1)


def prep(root: Path):
    edits: dict[str, str] = {}

    def modify(path: str, substitutions: list[tuple[str, str]]):
        target = root / path
        s = target.read_text(encoding="utf-8")
        for i, (old, new) in enumerate(substitutions):
            s = require_replace(s, old, new, f"{path} substitution {i+1}")
        edits[path] = s

    modify("bridge/core.py", [
        ("BRIDGE_REPO = 'Prithvi7007/crew-dev-bridge'\n",
         "BRIDGE_REPO = 'Prithvi7007/crew-dev-bridge'\n"
         "WEDDING_REPO = 'Prithvi7007/Wedding_Website_Final'\n"
         "WEDDING_WORKSPACE = Path(os.environ.get('WEDDING_WORKSPACE', '/wedding-source'))\n"),
        ("TOKEN_FILE = Path(os.environ.get('CREW_GITHUB_TOKEN_FILE', '/run/crew-secrets/github_token'))\n",
         "TOKEN_FILE = Path(os.environ.get('CREW_GITHUB_TOKEN_FILE', '/run/crew-secrets/github_token'))\n"
         "WEDDING_TOKEN_FILE = Path(os.environ.get('WEDDING_GITHUB_TOKEN_FILE', '/run/crew-secrets/wedding_github_token'))\n"),
        ("if repository not in ('crew_games', 'crew-dev-bridge'):",
         "if repository not in ('crew_games', 'crew-dev-bridge', 'wedding'):"),
        ("def _repo() -> str:\n    return REPO if _ACTIVE_REPOSITORY.get() == 'crew_games' else BRIDGE_REPO\n\n\ndef _workspace() -> Path:\n    return WORKSPACE if _ACTIVE_REPOSITORY.get() == 'crew_games' else BRIDGE_WORKSPACE\n",
         "def _repo() -> str:\n"
         "    selected = _ACTIVE_REPOSITORY.get()\n"
         "    return {'crew_games': REPO, 'crew-dev-bridge': BRIDGE_REPO, 'wedding': WEDDING_REPO}[selected]\n\n\n"
         "def _workspace() -> Path:\n"
         "    selected = _ACTIVE_REPOSITORY.get()\n"
         "    return {'crew_games': WORKSPACE, 'crew-dev-bridge': BRIDGE_WORKSPACE, 'wedding': WEDDING_WORKSPACE}[selected]\n\n\n"
         "def _base_branch() -> str:\n"
         "    return 'v3-production' if _ACTIVE_REPOSITORY.get() == 'wedding' else 'main'\n"),
        ("    if not TOKEN_FILE.is_file():\n        raise BridgeError('GitHub credential not provisioned: add repository-scoped token')\n    token = TOKEN_FILE.read_text().strip()",
         "    token_file = WEDDING_TOKEN_FILE if _ACTIVE_REPOSITORY.get() == 'wedding' else TOKEN_FILE\n"
         "    if not token_file.is_file():\n        raise BridgeError('GitHub credential not provisioned: add repository-scoped token')\n"
         "    token = token_file.read_text().strip()"),
        ("        main = github_request('GET', 'git/ref/heads/main')\n        sha = main['object']['sha']",
         "        base = github_request('GET', 'git/ref/heads/' + _base_branch())\n        sha = base['object']['sha']"),
        ("    allowed = ('pytest', 'react_build') if _ACTIVE_REPOSITORY.get() == 'crew_games' else ('pytest', 'bridge_syntax')",
         "    checks = {'crew_games': ('pytest', 'react_build'),\n"
         "              'crew-dev-bridge': ('pytest', 'bridge_syntax'),\n"
         "              'wedding': ('pytest', 'wedding_syntax')}\n"
         "    allowed = checks[_ACTIVE_REPOSITORY.get()]"),
        ("        second_check = 'react_build' if _ACTIVE_REPOSITORY.get() == 'crew_games' else 'bridge_syntax'",
         "        second_check = {'crew_games': 'react_build',\n"
         "                        'crew-dev-bridge': 'bridge_syntax',\n"
         "                        'wedding': 'wedding_syntax'}[_ACTIVE_REPOSITORY.get()]"),
        ("                else 'Both pytest and bridge_syntax must pass for the current changes'",
         "                else f'Both pytest and {second_check} must pass for the current changes'"),
        ("'base': 'main', 'draft': True", "'base': _base_branch(), 'draft': True"),
    ])

    modify("runner/worker.py", [
        ("ALLOWED = {'pytest', 'react_build', 'bridge_syntax'}",
         "ALLOWED = {'pytest', 'react_build', 'bridge_syntax', 'wedding_syntax'}"),
        ("if repository not in ('Prithvi7007/crew_games', 'Prithvi7007/crew-dev-bridge'):",
         "if repository not in ('Prithvi7007/crew_games', 'Prithvi7007/crew-dev-bridge', 'Prithvi7007/Wedding_Website_Final'):"),
        ("    if (kind == 'bridge_syntax' and repository != 'Prithvi7007/crew-dev-bridge') or (kind == 'react_build' and repository != 'Prithvi7007/crew_games'):",
         "    if (kind == 'bridge_syntax' and repository != 'Prithvi7007/crew-dev-bridge') or (kind == 'react_build' and repository != 'Prithvi7007/crew_games') or (kind == 'wedding_syntax' and repository != 'Prithvi7007/Wedding_Website_Final'):\n"
         "        raise ValueError('Check kind does not match repository')\n"
         "    if repository == 'Prithvi7007/Wedding_Website_Final' and kind not in {'pytest', 'wedding_syntax'}:"),
        ("        if kind == 'pytest':\n            cmd, cwd = ['python', '-m', 'pytest', '-q'], working\n        elif kind == 'bridge_syntax':",
         "        if kind == 'pytest':\n"
         "            python = '/opt/wedding-venv/bin/python' if repository == 'Prithvi7007/Wedding_Website_Final' else 'python'\n"
         "            cmd, cwd = [python, '-m', 'pytest', '-q'], working\n"
         "        elif kind == 'wedding_syntax':\n"
         "            cmd, cwd = ['/opt/wedding-venv/bin/python', '-m', 'compileall', '-q', 'app', 'tests', 'wsgi.py'], working\n"
         "        elif kind == 'bridge_syntax':"),
    ])

    modify("runner/Dockerfile", [
        ("COPY requirements.txt /tmp/crew_requirements.txt\n",
         "COPY requirements.txt /tmp/crew_requirements.txt\n"
         "COPY wedding-requirements.txt /tmp/wedding_requirements.txt\n"),
        ("COPY frontend/package.json frontend/package-lock.json /opt/frontend/",
         "RUN python3 -m venv /opt/wedding-venv && /opt/wedding-venv/bin/pip install --no-cache-dir -r /tmp/wedding_requirements.txt\n"
         "COPY frontend/package.json frontend/package-lock.json /opt/frontend/"),
    ])

    modify("compose.yaml", [
        ("      CREW_BRIDGE_WORKSPACE: /bridge-source\n",
         "      CREW_BRIDGE_WORKSPACE: /bridge-source\n"
         "      WEDDING_WORKSPACE: /wedding-source\n"
         "      WEDDING_GITHUB_TOKEN_FILE: /run/crew-secrets/wedding_github_token\n"),
        ("      - ./bridge-source:/bridge-source:rw\n",
         "      - ./bridge-source:/bridge-source:rw\n"
         "      - ./wedding-source:/wedding-source:rw\n"),
    ])

    modify("scripts/bootstrap.sh", [
        ("if [ ! -d source/.git ]; then\n  git clone --no-tags --branch main https://github.com/Prithvi7007/crew_games.git source\nfi\n",
         "if [ ! -d source/.git ]; then\n"
         "  git clone --no-tags --branch main https://github.com/Prithvi7007/crew_games.git source\n"
         "fi\n"
         "if [ -e wedding-source ] && [ ! -d wedding-source/.git ]; then\n"
         "  echo 'wedding-source/ exists but is not a Git checkout; refusing to overwrite.' >&2\n"
         "  exit 1\n"
         "fi\n"
         "if [ ! -d wedding-source/.git ]; then\n"
         "  git clone --no-tags --branch v3-production https://github.com/Prithvi7007/Wedding_Website_Final.git wedding-source\n"
         "fi\n"
         "if [ ! -f secrets/wedding_github_token ]; then\n"
         "  echo 'Create a fine-grained GitHub token scoped ONLY to Prithvi7007/Wedding_Website_Final.'\n"
         "  echo 'Repository permissions: Contents read/write; Pull requests read/write; Metadata read.'\n"
         "  read -r -s -p 'Wedding repository token (input hidden): ' wedding_token; echo\n"
         "  if [ -z \"$wedding_token\" ]; then echo 'Missing wedding token' >&2; exit 1; fi\n"
         "  (umask 077; printf '%s\n' \"$wedding_token\" > secrets/wedding_github_token)\n"
         "  unset wedding_token\n"
         "fi\n"),
        ("cp source/requirements.txt .runner-context/requirements.txt\n",
         "cp source/requirements.txt .runner-context/requirements.txt\n"
         "cp wedding-source/requirements.txt .runner-context/wedding-requirements.txt\n"),
        ("chown -R 10001:10001 source queue secrets audit\n",
         "chown -R 10001:10001 source wedding-source queue secrets audit\n"),
        ("chmod 0600 secrets/github_token\n",
         "chmod 0600 secrets/github_token secrets/wedding_github_token\n"),
    ])

    modify(".gitignore", [
        ("/bridge-source/\n", "/bridge-source/\n/wedding-source/\n"),
    ])

    modify("bridge/server.py", [
        ("Queue either pytest or react_build in the separate offline sandbox (no arbitrary shell).",
         "Queue repository-specific allowlisted checks in the offline sandbox (no arbitrary shell)."),
        ("Create a commit via GitHub API on feature/* only, requiring passing pytest and React build. Never pushes main.",
         "Commit feature/* through GitHub after repository-specific checks pass. Never pushes a release branch."),
        ("Create a draft GitHub PR from feature/* into main. No merging or deployment capability.",
         "Create a draft PR from feature/* into the repository's approved base. No merging or deployment capability."),
    ])

    modify("README.md", [
        ("A private MCP development bridge for `Prithvi7007/crew_games` and `Prithvi7007/crew-dev-bridge`.",
         "A private MCP development bridge for `Prithvi7007/crew_games`, `Prithvi7007/crew-dev-bridge`, and `Prithvi7007/Wedding_Website_Final`. See [the wedding integration guide](docs/wedding_bridge.md)."),
        ("- Fixed repo only: `Prithvi7007/crew_games`; fixed base branch: `main`.",
         "- Fixed, allowlisted repositories only: CREW, the development bridge, and Wedding V3. CREW/bridge target `main`; Wedding V3 targets `v3-production`."),
    ])

    tests = '''"""Regression tests for the third, independently-scoped development target."""
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
'''
    path = "tests/test_wedding_target.py"
    if (root / path).exists():
        raise RuntimeError(f"Will not overwrite existing {path}")
    edits[path] = tests

    doc = '''# Wedding V3 development bridge target

This change enables **development-only** access to `Prithvi7007/Wedding_Website_Final`,
with base branch `v3-production`. It does not grant wedding production release,
PostgreSQL access, systemd control, or unrestricted VPS commands.

## Separation guarantees

- Allowed repository ID: `wedding`, fixed to `Prithvi7007/Wedding_Website_Final`.
- Separate `wedding-source/` checkout and `secrets/wedding_github_token` (never commit).
- Separate offline Python test environment in the runner.
- Wedding checks: `pytest` and `wedding_syntax`; both required before a GitHub push.
- Draft PRs target `v3-production`, not the legacy `main` branch.
- No changes to CREW release infrastructure, database, or systemd services.

## Controlled host activation

1. Review and merge this PR only after pytest, syntax tests, compose validation,
   and a release-workflow review.
2. Back up `/opt/crew-dev-bridge` configuration and directories.
3. `git fetch` / update the **bridge checkout** on the VPS with the merged commit.
4. Review `scripts/bootstrap.sh` before executing. It provisions an independent
   wedding-scoped PAT (Contents RW, Pull Requests RW, Metadata R). Never paste
   a PAT into ChatGPT or commit it to a repository.
5. Rebuild only the bridge/runner containers; do not restart `crew.service`
   or `wedding.service` or change production databases.
6. Verify `crew_repo_status(repository='wedding')`, run both check kinds on a
   disposable feature branch, open a draft PR, and verify isolation.

## Not yet implemented

The wedding **production** release agent must be independently designed and
installed with its own database backup/restore, staging rehearsal, CI gate,
service allowlist, non-destructive migrations and health/rollback checks.
A read-only `wedding_db` reporting role should be provisioned separately and
expose only approved aggregate/reporting views; no invitation tokens, personal
contact details, or guest-level notes are returned to ChatGPT by default.
'''
    path = "docs/wedding_bridge.md"
    if (root / path).exists():
        raise RuntimeError(f"Will not overwrite existing {path}")
    edits[path] = doc
    return edits


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    if not (root / "bridge/core.py").is_file():
        raise SystemExit("Run from a crew-dev-bridge repository checkout")
    result = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                            capture_output=True, text=True, check=True)
    if result.stdout.strip():
        raise SystemExit("Refusing to edit a dirty checkout; commit or stash changes first")
    branch = subprocess.check_output(["git", "branch", "--show-current"],
                                     cwd=root, text=True).strip()
    if branch in ("main", "master") or not branch.startswith("feature/"):
        raise SystemExit("First create and checkout a feature/* branch in the bridge repository")
    edits = prep(root)  # All substitutions must validate before writing a single file.
    for name, content in edits.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    print(f"Prepared {len(edits)} changed files. No credentials or production services touched.")
    print("Next: git diff --check && python3 -m pytest -q && docker compose config -q")
    print("Review git diff before committing. Do not deploy before tests pass.")


if __name__ == "__main__":
    main()
