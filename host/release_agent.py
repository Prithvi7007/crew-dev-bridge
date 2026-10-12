"""Reusable, fixed-target CREW release worker. Install as root-owned host code only.

No client-supplied shell, path, commit, database, or action flags. The target is
the GitHub-verified origin/main head with a successful quality-gate check.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
from urllib import request as urllib_request
from urllib.parse import urlsplit, urlunsplit, unquote

ROOT = Path("/opt/crew")
BACKUPS = Path("/var/backups/crew")
STATE_DIR = Path("/var/lib/crew-release")
WORKTREE = STATE_DIR / "worktrees" / "candidate"
STATE_FILE = STATE_DIR / "status.json"
ENV_FILE = Path("/etc/crew/crew.env")
SOCKET = Path("/run/crew-release/release.sock")
HOST_UID = int(os.environ.get("CREW_RELEASE_BRIDGE_UID", "10001"))
ORIGIN = "https://github.com/Prithvi7007/crew_games.git"
REPO_API = "https://api.github.com/repos/Prithvi7007/crew_games"
EXPECTED_CHECK = "test-and-audit"
DB_PROD = "crew_prod"
DB_SCRATCH = "crew_release_verify"
VALID_SHA = re.compile(r"[a-f0-9]{40}\Z")
VALID_REV = re.compile(r"v[a-z0-9_]+\Z")
STATUS_LIMIT = 4096
_lock = threading.Lock()
_active = False


def _reply(ok, **fields):
    return {"ok": bool(ok), **fields}


class ReleaseError(Exception):
    pass


def _run(argv, *, env=None, timeout=90, user=None):
    """Never use a shell; log no stdout or credentials from subprocess failures."""
    cmd = [str(a) for a in argv]
    if user:
        cmd = ["runuser", "-u", user, "--", *cmd]
    p = subprocess.run(
        cmd, env=env or {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        capture_output=True, text=True, timeout=timeout, check=False,
        stdin=subprocess.DEVNULL,
    )
    if p.returncode:
        raise ReleaseError("command_failed:" + Path(cmd[-len(argv)]).name)
    return p.stdout.strip()


def _git(*args, cwd=ROOT, timeout=60):
    return _run(["git", "-c", "core.hooksPath=/dev/null",
                 "-c", "core.fsmonitor=false", "-c", "safe.directory=" + str(cwd),
                 "-C", str(cwd), *args], user="crew", timeout=timeout,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                     "HOME": "/tmp", "GIT_TERMINAL_PROMPT": "0",
                     "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"})


def _load_env():
    st = ENV_FILE.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        raise ReleaseError("unsafe_environment_file")
    config = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ReleaseError("invalid_environment_file")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ReleaseError("invalid_environment_file")
        value = value.strip()
        if value.startswith(("'", '"')):
            try:
                parts = shlex.split(value, comments=False, posix=True)
            except ValueError as exc:
                raise ReleaseError("invalid_environment_file") from exc
            if len(parts) != 1:
                raise ReleaseError("invalid_environment_file")
            value = parts[0]
        config[key] = value
    url = config.get("DATABASE_URL", "")
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgres", "postgresql", "postgresql+psycopg"):
        raise ReleaseError("unsupported_db_url")
    if unquote(parsed.path.lstrip("/")) != DB_PROD or parsed.hostname not in (
        "127.0.0.1", "localhost", "::1", "/var/run/postgresql",
    ) or (parsed.port or 5432) != 5432 or unquote(parsed.username or "") != "crew_app":
        raise ReleaseError("database_scope_mismatch")
    if not parsed.password:
        raise ReleaseError("missing_db_password")
    base = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp",
            **config, "AUTO_DB_MIGRATE": "false",
            "PGHOST": parsed.hostname, "PGPORT": str(parsed.port or 5432),
            "PGUSER": "crew_app", "PGPASSWORD": unquote(parsed.password),
            "PGCONNECT_TIMEOUT": "5"}
    return base, parsed


def _env_for_db(base, parsed, db, *, rehearsal=False):
    e = dict(base)
    e["PGDATABASE"] = db
    e["DATABASE_URL"] = urlunsplit((parsed.scheme, parsed.netloc, "/" + db,
                                     parsed.query, parsed.fragment))
    if rehearsal:
        e["CREW_ENV"] = "development"
        e["SESSION_COOKIE_SECURE"] = "false"
    return e


def _counts(env):
    statement = ("SELECT (SELECT count(*) FROM profiles)::text || ':' || "
                 "(SELECT count(*) FROM game_completions)::text || ':' || "
                 "(SELECT count(*) FROM user_stats)::text || ':' || "
                 "(SELECT count(*) FROM badge_awards)::text")
    return _run(["psql", "-X", "-Atq", "-v", "ON_ERROR_STOP=1",
                 "-c", statement], env=env, timeout=20)


def _counts_preserved(before, after):
    """Allow new player activity during rehearsals, never missing saved records."""
    try:
        older = [int(value) for value in before.split(":")]
        newer = [int(value) for value in after.split(":")]
    except (ValueError, AttributeError):
        return False
    return len(older) == len(newer) == 4 and all(n >= o for o, n in zip(older, newer))


def _revision(env):
    return _run(["psql", "-X", "-Atq", "-v", "ON_ERROR_STOP=1",
                 "-c", "SELECT version_num FROM alembic_version"],
                env=env, timeout=15)


def _target():
    if _git("remote", "get-url", "origin") not in (ORIGIN, ORIGIN[:-4], "git@github.com:Prithvi7007/crew_games.git"):
        raise ReleaseError("unexpected_git_remote")
    if _git("status", "--porcelain"):
        raise ReleaseError("dirty_production_checkout")
    current = _git("rev-parse", "HEAD")
    if not VALID_SHA.fullmatch(current):
        raise ReleaseError("invalid_current_sha")
    remote = _git("ls-remote", "origin", "refs/heads/main").split()
    if len(remote) != 2 or remote[1] != "refs/heads/main" or not VALID_SHA.fullmatch(remote[0]):
        raise ReleaseError("unverified_main")
    return current, remote[0]


def _ci_verified(sha):
    req = urllib_request.Request(
        f"{REPO_API}/commits/{sha}/check-runs?per_page=100",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "crew-release-agent",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib_request.urlopen(req, timeout=12) as response:
        data = json.load(response)
    checks = data.get("check_runs", [])
    return any(c.get("name") == EXPECTED_CHECK and
               c.get("conclusion") == "success" and
               c.get("status") == "completed" for c in checks)


def _expected_revision(worktree):
    data = (worktree / "app/migrations.py").read_text()
    matches = re.findall(r'^HEAD_REVISION\s*=\s*"([^"]+)"\s*$', data, flags=re.M)
    if len(matches) != 1 or not VALID_REV.fullmatch(matches[0]):
        raise ReleaseError("invalid_release_schema")
    return matches[0]


def _check_additive_migrations(worktree, existing_revision):
    """Fail closed on destructive migration primitives in pending scripts.

    Rehearsal and preserved-table counts provide additional independent guards.
    """
    versions = worktree / "alembic/versions"
    migration_files = list(versions.glob("*.py"))
    graph = {}
    for path in migration_files:
        contents = path.read_text()
        rev = re.search(r'^revision\s*=\s*"([^"]+)"', contents, re.M)
        parent = re.search(r'^down_revision\s*=\s*"([^"]+)"', contents, re.M)
        if rev and parent:
            graph[rev[1]] = (parent[1], contents)
    head = _expected_revision(worktree)
    pending = []
    while head != existing_revision:
        if head not in graph or len(pending) > 50:
            raise ReleaseError("invalid_migration_chain")
        parent, code = graph[head]
        upgrade_only = code.split("def upgrade()", 1)[-1].split("def downgrade()", 1)[0]
        if re.search(r'\b(drop_table|drop_column|drop_index|execute|alter_column|DROP\s+TABLE|DELETE\s+FROM|TRUNCATE)\b', upgrade_only, re.I):
            raise ReleaseError("non_additive_migration_requires_operator_review")
        pending.append(head)
        head = parent
    return pending


def _check_release_files(old_sha, target_sha):
    """Compiled React bundles are committed; runtime dependencies stay pinned."""
    for rel in ("requirements.txt", "frontend/package.json", "frontend/package-lock.json"):
        source = ROOT / rel
        candidate = WORKTREE / rel
        if source.exists() != candidate.exists() or (
                source.exists() and source.read_bytes() != candidate.read_bytes()):
            raise ReleaseError("dependency_change_requires_operator_review")
    changed = _git("diff", "--name-only", old_sha, target_sha).splitlines()
    if any(name.startswith("frontend/") for name in changed):
        if not any(name in ("app/static/react/player.js", "app/static/react/player.css")
                   for name in changed):
            raise ReleaseError("frontend_assets_not_committed")


def _stage(commit):
    if WORKTREE.exists() or WORKTREE.is_symlink():
        raise ReleaseError("stale_release_worktree")
    _git("fetch", "--no-tags", "origin", "refs/heads/main", timeout=100)
    fetched = _git("rev-parse", "FETCH_HEAD")
    if fetched != commit:
        raise ReleaseError("remote_main_changed")
    _git("merge-base", "--is-ancestor", "HEAD", commit)
    WORKTREE.parent.mkdir(parents=True, exist_ok=True)
    # The installer makes this parent owned by crew.
    _git("worktree", "add", "--detach", str(WORKTREE), commit, timeout=100)
    if _git("rev-parse", "HEAD", cwd=WORKTREE) != commit:
        raise ReleaseError("staging_sha_mismatch")


def _remove_stage():
    if WORKTREE.exists():
        _git("worktree", "remove", "--force", str(WORKTREE), timeout=30)


def _backup(env):
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tmp = BACKUPS / ("crew_release_" + stamp + ".dump.tmp")
    final = BACKUPS / ("crew_release_" + stamp + ".dump")
    if final.exists() or tmp.exists():
        raise ReleaseError("backup_collision")
    try:
        _run(["pg_dump", "-Fc", "--no-owner", "--no-acl",
              "-f", str(tmp)], env=env, timeout=150)
        _run(["pg_restore", "--list", str(tmp)], timeout=20)
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        tmp.chmod(0o600)
        tmp.replace(final)
        (final.parent / (final.name + ".sha256")).write_text(digest + "  " + final.name + "\n")
        (final.parent / (final.name + ".sha256")).chmod(0o600)
        return final
    finally:
        tmp.unlink(missing_ok=True)


def _postgres(*argv):
    return _run(list(argv), user="postgres", timeout=80,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})


def _flask_in(worktree, env):
    cmd = ["runuser", "-u", "crew", "--",
           str(ROOT / ".venv/bin/python"), "-m", "flask", "--app", "run.py", "db-upgrade"]
    # cwd must be staged tree, not old production files.
    result = subprocess.run(cmd, cwd=worktree, env=env, stdin=subprocess.DEVNULL,
                            text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise ReleaseError("migration_failed")


def _health():
    result = _run(["curl", "--fail", "--silent", "--show-error",
                   "--max-time", "15", "--unix-socket", "/run/crew/crew.sock",
                   "-H", "Host: cadacrew.fun", "http://localhost/health/ready"],
                  timeout=20)
    if json.loads(result).get("status") != "ok":
        raise ReleaseError("health_check_failed")


def _save(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    tmp.chmod(0o600)
    tmp.replace(STATE_FILE)


def _read():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"state": "idle"}


def _update(job, step, **extra):
    data = {"job_id": job, "state": "running", "step": step, **extra}
    _save(data)


def _run_release(job):
    global _active
    stage_added = False
    scratch_created = False
    code_updated = False
    old_commit = ""
    before_counts = ""
    expected_rev = ""
    backup = None
    try:
        _update(job, "verify_main_and_ci")
        old_commit, target = _target()
        if old_commit == target:
            raise ReleaseError("already_deployed")
        if not _ci_verified(target):
            raise ReleaseError("quality_gate_not_green")
        _update(job, "stage_merged_release", target=target)
        _stage(target)
        stage_added = True
        _check_release_files(old_commit, target)
        base, parsed = _load_env()
        prod_env = _env_for_db(base, parsed, DB_PROD)
        scratch_env = _env_for_db(base, parsed, DB_SCRATCH, rehearsal=True)
        before_revision = _revision(prod_env)
        pending = _check_additive_migrations(WORKTREE, before_revision)
        expected_rev = _expected_revision(WORKTREE)
        before_counts = _counts(prod_env)
        _update(job, "verified_backup", target=target, migrations=pending)
        backup = _backup(prod_env)
        if hashlib.sha256(backup.read_bytes()).hexdigest() not in (
                (backup.parent / (backup.name + ".sha256")).read_text()):
            raise ReleaseError("backup_hash_mismatch")
        _update(job, "rehearse_migration", target=target, migrations=pending)
        existing = _postgres("psql", "-X", "-Atq", "-d", "postgres",
                             "-c", f"SELECT count(*) FROM pg_database WHERE datname='{DB_SCRATCH}'")
        if existing != "0":
            raise ReleaseError("scratch_db_already_exists")
        _postgres("createdb", "--owner=crew_app", DB_SCRATCH)
        scratch_created = True
        _run(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
              "-d", DB_SCRATCH, str(backup)], env=scratch_env, timeout=160)
        snapshot_counts = _counts(scratch_env)
        if _revision(scratch_env) != before_revision or not _counts_preserved(before_counts, snapshot_counts):
            raise ReleaseError("rehearsal_backup_inconsistent")
        if pending:
            _flask_in(WORKTREE, scratch_env)
        if _revision(scratch_env) != expected_rev or _counts(scratch_env) != snapshot_counts:
            raise ReleaseError("rehearsal_invariant_failed")
        _postgres("dropdb", DB_SCRATCH)
        scratch_created = False
        _update(job, "migrate_production", target=target, migrations=pending)
        # Revalidate target and counts before changing the live database.
        current, remote = _target()
        if current != old_commit or remote != target or not _counts_preserved(snapshot_counts, _counts(prod_env)):
            raise ReleaseError("production_changed_during_rehearsal")
        if pending:
            _flask_in(WORKTREE, prod_env)
        if _revision(prod_env) != expected_rev or not _counts_preserved(snapshot_counts, _counts(prod_env)):
            raise ReleaseError("production_migration_invariant_failed")
        _update(job, "deploy_code", target=target, migrations=pending)
        _git("reset", "--hard", target)
        code_updated = True
        _update(job, "restart_and_health", target=target, migrations=pending)
        _run(["systemctl", "restart", "crew.service"], timeout=60)
        _health()
        if _git("rev-parse", "HEAD") != target:
            raise ReleaseError("deployed_sha_mismatch")
        _save({"job_id": job, "state": "passed", "step": "complete",
               "target": target, "database_revision": expected_rev,
               "backup_created": True})
    except Exception as exc:
        error = str(exc) if isinstance(exc, ReleaseError) else type(exc).__name__
        if code_updated:
            try:
                _git("reset", "--hard", old_commit)
                _run(["systemctl", "restart", "crew.service"], timeout=60)
                _health()
                error += ":code_rolled_back"
            except Exception:
                error += ":manual_recovery_required"
        _save({"job_id": job, "state": "failed", "step": "halted",
               "error": error[:160], "backup_created": bool(backup),
               "database_revision": expected_rev or None})
    finally:
        if scratch_created:
            try:
                _postgres("dropdb", DB_SCRATCH)
            except Exception:
                pass
        if stage_added:
            try:
                _remove_stage()
            except Exception:
                pass
        with _lock:
            _active = False


def plan():
    try:
        current, target = _target()
        base, parsed = _load_env()
        revision = _revision(_env_for_db(base, parsed, DB_PROD))
        return _reply(True, production_commit=current, target_commit=target,
                      db_revision=revision, update_pending=current != target,
                      ci_passed=_ci_verified(target),
                      release_agent="reusable_v1", mode="additive_migrations")
    except Exception as exc:
        return _reply(False, error=str(exc) if isinstance(exc, ReleaseError)
                      else "preflight_unavailable")


def dispatch(data):
    global _active
    if not isinstance(data, dict) or set(data) != {"operation"}:
        return _reply(False, error="invalid_request")
    op = data["operation"]
    if op == "capabilities":
        return _reply(True, operations=["capabilities", "plan", "start", "status"],
                      release_agent="reusable_v1",
                      approval_gate="merged_main_and_successful_github_ci")
    if op == "plan":
        return plan()
    if op == "status":
        return _reply(True, **_read())
    if op == "start":
        with _lock:
            if _active or _read().get("state") == "running":
                return _reply(False, error="release_already_running_or_interrupted")
            _active = True
            job = hashlib.sha256(os.urandom(32)).hexdigest()[:20]
            _update(job, "queued")
            threading.Thread(target=_run_release, args=(job,), daemon=True).start()
        return _reply(True, job_id=job, state="queued")
    return _reply(False, error="operation_not_allowed")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(20)
            peer = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _, uid, _ = struct.unpack("3i", peer)
            if uid != HOST_UID:
                result = _reply(False, error="peer_not_authorized")
            else:
                raw = self.rfile.readline(4097)
                if not raw.endswith(b"\n") or len(raw) > 4096:
                    result = _reply(False, error="invalid_request")
                else:
                    result = dispatch(json.loads(raw))
            wire = json.dumps(result, separators=(",", ":")).encode()
            self.wfile.write((wire if len(wire) <= STATUS_LIMIT else
                              b'{"ok":false,"error":"response_too_large"}') + b"\n")
        except (OSError, ValueError, TypeError):
            pass


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 8


def main():
    if os.geteuid() != 0:
        raise RuntimeError("release agent requires root-owned host installation")
    if SOCKET.is_symlink():
        raise RuntimeError("unsafe_release_socket")
    if SOCKET.exists():
        if not stat.S_ISSOCK(SOCKET.stat().st_mode):
            raise RuntimeError("unexpected_release_socket")
        SOCKET.unlink()
    if _read().get("state") == "running":
        _save({**_read(), "state": "interrupted",
               "error": "host_service_restart_during_release"})
    os.umask(0o077)
    with Server(str(SOCKET), Handler) as server:
        SOCKET.chmod(0o660)
        server.serve_forever(poll_interval=.5)


if __name__ == "__main__":
    main()
