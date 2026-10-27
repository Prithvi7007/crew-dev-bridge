"""Restricted single-repository CREW development workspace.

No arbitrary shell, URLs, production paths, or direct main-branch writes.
Repository source is untrusted: never run it from this process.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

REPO = os.environ.get('CREW_REPO', 'Prithvi7007/crew_games')
WORKSPACE = Path(os.environ.get('CREW_WORKSPACE', '/workspace'))
BRIDGE_WORKSPACE = Path(os.environ.get('CREW_BRIDGE_WORKSPACE', '/bridge-source'))
BRIDGE_REPO = 'Prithvi7007/crew-dev-bridge'
WEDDING_REPO = 'Prithvi7007/Wedding_Website_Final'
WEDDING_WORKSPACE = Path(os.environ.get('WEDDING_WORKSPACE', '/wedding-source'))
_ACTIVE_REPOSITORY = ContextVar('crew_active_repository', default='crew_games')
QUEUE = Path(os.environ.get('CREW_QUEUE', '/queue'))
TOKEN_FILE = Path(os.environ.get('CREW_GITHUB_TOKEN_FILE', '/run/crew-secrets/github_token'))
WEDDING_TOKEN_FILE = Path(os.environ.get('WEDDING_GITHUB_TOKEN_FILE', '/run/crew-secrets/wedding_github_token'))
BRANCH_RE = re.compile(r'^feature/[a-z0-9][a-z0-9._/-]{1,70}$')
MAX_FILE = 250_000
# Only the checksum-verified compiled player entry may exceed the source-file cap.
MAX_PLAYER_BUNDLE = 450_000


def max_file_size(path: str) -> int:
    return MAX_PLAYER_BUNDLE if path == 'app/static/react/player.js' else MAX_FILE

MAX_CHANGES = 30
EXCLUDED_PARTS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.ssh', '.aws', 'secrets'}
EXCLUDED_NAMES = {'.env', '.env.local', '.env.production', 'id_rsa', 'id_ed25519', '.npmrc', '.pypirc', '.netrc'}
LOCK = Path(os.environ.get('CREW_LOCK', '/queue/workspace.lock'))
AUDIT = Path(os.environ.get('CREW_AUDIT', '/audit/events.jsonl'))

class BridgeError(Exception):
    pass


@contextmanager
def repository_context(repository: str = 'crew_games'):
    """Select only the fixed repositories, isolated per request/thread."""
    if repository not in ('crew_games', 'crew-dev-bridge', 'wedding'):
        raise BridgeError('Unsupported repository')
    token = _ACTIVE_REPOSITORY.set(repository)
    try:
        yield
    finally:
        _ACTIVE_REPOSITORY.reset(token)


def _repo() -> str:
    selected = _ACTIVE_REPOSITORY.get()
    return {'crew_games': REPO, 'crew-dev-bridge': BRIDGE_REPO, 'wedding': WEDDING_REPO}[selected]


def _workspace() -> Path:
    selected = _ACTIVE_REPOSITORY.get()
    return {'crew_games': WORKSPACE, 'crew-dev-bridge': BRIDGE_WORKSPACE, 'wedding': WEDDING_WORKSPACE}[selected]


def _base_branch() -> str:
    return 'main'


def audit(event: str, **details) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({'timestamp': int(time.time()), 'event': event, 'repository': _repo(), **details}, sort_keys=True) + '\n'
    fd = os.open(AUDIT, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode('utf-8'))
    finally:
        os.close(fd)


def safe_path(path: str, *, must_exist: bool = False) -> Path:
    if not isinstance(path, str) or not path or len(path) > 220 or '\\' in path or '\x00' in path:
        raise BridgeError('Invalid repository-relative path')
    pp = PurePosixPath(path)
    if pp.is_absolute() or path.startswith('./') or any(p in {'.', '..'} or p in EXCLUDED_PARTS for p in pp.parts):
        raise BridgeError('Path outside permitted source files')
    if any(p.startswith('.env') or p in EXCLUDED_NAMES for p in pp.parts):
        raise BridgeError('Protected configuration or secret path')
    if pp.parts[:2] == ('.github', 'workflows'):
        raise BridgeError('Workflow changes require manual review outside the bridge')
    root = _workspace().resolve()
    cursor = root
    for part in pp.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BridgeError('Symlinks are not accepted as source paths')
    target = (root / path).resolve(strict=False)
    if not target.is_relative_to(root):
        raise BridgeError('Path escapes workspace (possibly through a symlink)')
    if must_exist and (not target.is_file() or target.is_symlink()):
        raise BridgeError('File does not exist or is not regular')
    return target


def validate_branch(name: str) -> str:
    if not isinstance(name, str) or not BRANCH_RE.fullmatch(name) or '..' in name or '//' in name or name.endswith(('/', '.')) or '/.' in name:
        raise BridgeError('Branch must start with feature/ and use safe lowercase characters')
    return name


def _git(*args: str, input: str | None = None, timeout: int = 30) -> str:
    if not _workspace().is_dir():
        raise BridgeError('Workspace missing: run bootstrap first')
    # Do not pass bridge credentials to a subprocess. Disable hooks and pagers.
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': '/tmp',
           'LANG': 'C.UTF-8', 'GIT_TERMINAL_PROMPT': '0',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0'}
    proc = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.pager=cat',
                           '-c', 'diff.external=', *args], cwd=_workspace(),
                          input=input, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode:
        raise BridgeError(f'Git command failed: {proc.stderr.strip()[:600]}')
    return proc.stdout.strip()


@contextmanager
def locked():
    QUEUE.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def branch() -> str:
    return _git('symbolic-ref', '--quiet', '--short', 'HEAD')


def changed_paths() -> list[str]:
    # Git reports changes in the working tree and untracked files; disallow rename
    # ambiguity by treating each destination as a regular path.
    modified = _git('diff', '--name-only', '--no-renames', 'HEAD').splitlines()
    untracked = _git('ls-files', '--others', '--exclude-standard').splitlines()
    paths = sorted(set(modified + untracked))
    if len(paths) > MAX_CHANGES:
        raise BridgeError('Too many files modified in one operation')
    for path in paths:
        safe_path(path)
    return paths


def fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(_repo().encode('utf-8') + b'\0')
    digest.update(branch().encode())
    for p in changed_paths():
        target = safe_path(p)
        digest.update(p.encode() + b'\0')
        if target.is_file():
            if target.stat().st_size > max_file_size(p):
                raise BridgeError('Oversized file: ' + p)
            digest.update(target.read_bytes())
        else:
            digest.update(b'<deleted>')
    return digest.hexdigest()


def status() -> dict:
    return {'repository': _repo(), 'branch': branch(), 'head': _git('rev-parse', 'HEAD'),
            'changes': changed_paths(), 'fingerprint': fingerprint(),
            'production_access': False}


def read_file(path: str) -> dict:
    target = safe_path(path, must_exist=True)
    raw = target.read_bytes()
    if len(raw) > max_file_size(path):
        raise BridgeError('File exceeds size limit')
    try:
        content = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise BridgeError('Only UTF-8 source files are available') from exc
    return {'path': path, 'sha256': hashlib.sha256(raw).hexdigest(), 'content': content}


def write_file(path: str, content: str, expected_sha256: str) -> dict:
    if not isinstance(content, str) or len(content.encode('utf-8')) > max_file_size(path):
        raise BridgeError('UTF-8 source exceeds allowed size limit')
    validate_branch(branch())
    target = safe_path(path)
    with locked():
        if target.exists():
            if not target.is_file() or target.is_symlink():
                raise BridgeError('Cannot modify special files or symlinks')
            current = hashlib.sha256(target.read_bytes()).hexdigest()
            if current != expected_sha256:
                raise BridgeError('File changed since last read; read it again')
        elif expected_sha256 != 'NEW':
            raise BridgeError('New files require expected_sha256="NEW"')
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name('.bridge-' + uuid.uuid4().hex)
        try:
            tmp.write_text(content, encoding='utf-8')
            os.chmod(tmp, target.stat().st_mode & 0o777 if target.exists() else 0o644)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        audit('write_file', branch=branch(), path=path)
        return {'path': path, 'sha256': hashlib.sha256(content.encode()).hexdigest(),
                'changed_paths': changed_paths()}


def diff() -> dict:
    output = _git('diff', '--no-ext-diff', '--no-color', 'HEAD', timeout=20)
    if len(output) > 32_000:
        output = output[:32_000] + '\n[TRUNCATED]'
    return {'branch': branch(), 'diff': output, 'untracked': _git('ls-files', '--others', '--exclude-standard').splitlines()}


def github_request(method: str, endpoint: str, payload: dict | None = None) -> dict:
    token_file = WEDDING_TOKEN_FILE if _ACTIVE_REPOSITORY.get() == 'wedding' else TOKEN_FILE
    if not token_file.is_file():
        raise BridgeError('GitHub credential not provisioned: add repository-scoped token')
    token = token_file.read_text().strip()
    if not token:
        raise BridgeError('GitHub credential is empty')
    # Endpoint supplied only by server-owned code, never user input.
    url = 'https://api.github.com/repos/' + _repo() + '/' + endpoint.lstrip('/')
    try:
        response = httpx.request(method, url, json=payload, timeout=25,
             headers={'Accept': 'application/vnd.github+json',
                      'X-GitHub-Api-Version': '2022-11-28',
                      'Authorization': 'Bearer ' + token})
    except httpx.HTTPError as exc:
        raise BridgeError('GitHub connection failed') from exc
    if response.status_code >= 400:
        # Never echo URLs, request headers, tokens or raw response bodies.
        raise BridgeError(f'GitHub returned HTTP {response.status_code} for {method} {endpoint.split("/")[0]}')
    return response.json() if response.content else {}


def create_branch(name: str) -> dict:
    validate_branch(name)
    with locked():
        if changed_paths():
            raise BridgeError('Workspace has uncommitted changes')
        base = github_request('GET', 'git/ref/heads/' + _base_branch())
        sha = base['object']['sha']
        github_request('POST', 'git/refs', {'ref': f'refs/heads/{name}', 'sha': sha})
        # Only fetch public branch content; authentication is not passed to git.
        _git('fetch', '--no-tags', 'origin', f'refs/heads/{name}', timeout=60)
        _git('checkout', '-B', name, 'FETCH_HEAD')
        audit('create_branch', branch=name, base=sha)
        return {'branch': name, 'base_sha': sha}



def apply_patch(patch: str) -> dict:
    """Apply bounded unified Git diff, with path and file-mode validation."""
    if not isinstance(patch, str) or not patch.startswith('diff --git ') or len(patch.encode('utf-8')) > 125_000:
        raise BridgeError('Expected a Git unified diff no larger than 125 KB')
    validate_branch(branch())
    headers = re.findall(r'^diff --git a/([^\n]+) b/([^\n]+)$', patch, re.MULTILINE)
    if not headers or len(headers) != patch.count('diff --git ') or len(headers) > MAX_CHANGES:
        raise BridgeError('Invalid patch headers or too many changes')
    names = set()
    for old, new in headers:
        if old != new or not re.fullmatch(r'[A-Za-z0-9_./-]+', old):
            raise BridgeError('Renames or unsafe patch paths are not supported')
        safe_path(old)
        names.add(old)
    forbidden = ('GIT binary patch', 'Binary files ', 'rename from ', 'rename to ',
                 'copy from ', 'copy to ', 'old mode ', 'new mode ',
                 'new file mode 120000', 'new file mode 100755')
    if any(line.startswith(forbidden) for line in patch.splitlines()):
        raise BridgeError('Binary files, symlinks, renames, and mode changes are blocked')
    for line in patch.splitlines():
        if line.startswith(('new file mode ', 'deleted file mode ')) and line.split()[-1] != '100644':
            raise BridgeError('Only standard text files are supported')
    with locked():
        if len(names | set(changed_paths())) > MAX_CHANGES:
            raise BridgeError('Patch exceeds file count limit')
        _git('apply', '--check', '--whitespace=error', '-', input=patch)
        _git('apply', '--whitespace=error', '-', input=patch)
        audit('apply_patch', branch=branch(), paths=sorted(names))
        return {'applied_paths': sorted(names), 'changed_paths': changed_paths(),
                'fingerprint': fingerprint()}


def _snapshot(target: Path) -> None:
    import shutil
    def ignore(_folder, names):
        return [name for name in names if name in {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache', 'dist', 'secrets'} or name.startswith('.env') or (Path(_folder) / name).is_symlink()]
    shutil.copytree(_workspace(), target, ignore=ignore, symlinks=False)


def start_check(kind: str) -> dict:
    checks = {'crew_games': ('pytest', 'react_build'),
              'crew-dev-bridge': ('pytest', 'bridge_syntax'),
              'wedding': ('pytest', 'wedding_syntax')}
    allowed = checks[_ACTIVE_REPOSITORY.get()]
    if kind not in allowed:
        raise BridgeError('Unsupported check kind for selected repository')
    with locked():
        validate_branch(branch())
        job_id = uuid.uuid4().hex
        job_dir = QUEUE / 'snapshots' / job_id
        (QUEUE / 'jobs').mkdir(parents=True, exist_ok=True)
        (QUEUE / 'results').mkdir(parents=True, exist_ok=True)
        _snapshot(job_dir)
        ticket = {'job_id': job_id, 'kind': kind, 'fingerprint': fingerprint(), 'branch': branch(),
                  'repository': _repo(),
                  'created_at': int(time.time())}
        tmp = QUEUE / 'jobs' / (job_id + '.tmp')
        tmp.write_text(json.dumps(ticket))
        os.replace(tmp, QUEUE / 'jobs' / (job_id + '.json'))
        audit('start_check', branch=branch(), kind=kind, job_id=job_id)
        return {'job_id': job_id, 'state': 'queued', 'kind': kind, 'fingerprint': ticket['fingerprint']}


def check_status(job_id: str) -> dict:
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise BridgeError('Invalid job id')
    result = QUEUE / 'results' / (job_id + '.json')
    if result.is_file():
        data = json.loads(result.read_text())
        if data.get('repository', REPO) != _repo():
            raise BridgeError('Check belongs to a different repository')
        return {k: data.get(k) for k in ('job_id', 'kind', 'state', 'exit_code', 'output', 'fingerprint', 'branch', 'repository')}
    queued = QUEUE / 'jobs' / (job_id + '.json')
    if queued.is_file():
        if json.loads(queued.read_text()).get('repository', REPO) != _repo():
            raise BridgeError('Check belongs to a different repository')
        return {'job_id': job_id, 'state': 'queued_or_running'}
    raise BridgeError('Unknown check id')


def _check_passed(kind: str, current: str) -> bool:
    results = QUEUE / 'results'
    if not results.is_dir():
        return False
    for result in results.glob('*.json'):
        try:
            data = json.loads(result.read_text())
            if (data.get('repository', REPO) == _repo() and data.get('kind') == kind
                    and data.get('state') == 'passed' and data.get('fingerprint') == current):
                return True
        except (OSError, ValueError):
            continue
    return False



def sync_frontend_assets(job_id: str) -> dict:
    """Import verified outputs of a passing isolated React build.

    This is intentionally limited to two checked-in UTF-8 build assets.
    It never runs repository code, accesses production, or commits anything.
    """
    import stat
    if _ACTIVE_REPOSITORY.get() != 'crew_games':
        raise BridgeError('React asset import is only allowed for crew_games')
    if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise BridgeError('Invalid job id')
    allowed = ('app/static/react/player.js', 'app/static/react/player.css')
    with locked():
        active = validate_branch(branch())
        result_path = QUEUE / 'results' / (job_id + '.json')
        if not result_path.is_file():
            raise BridgeError('Build result not found')
        result = json.loads(result_path.read_text())
        if (result.get('job_id') != job_id or result.get('kind') != 'react_build'
                or result.get('state') != 'passed' or result.get('exit_code') != 0
                or result.get('branch') != active or result.get('fingerprint') != fingerprint()):
            raise BridgeError('Build result does not match current workspace')
        root = QUEUE / 'artifacts' / job_id
        manifest_path = root / 'manifest.json'
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise BridgeError('Missing artifact manifest')
        manifest = json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict) or set(manifest) != set(allowed):
            raise BridgeError('Unexpected artifact manifest')
        pending = {}
        for rel in allowed:
            source = root / Path(rel).name
            st = source.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_size > max_file_size(rel):
                raise BridgeError('Unsafe or oversized build output')
            raw = source.read_bytes()
            raw.decode('utf-8')
            if hashlib.sha256(raw).hexdigest() != manifest[rel]:
                raise BridgeError('Build artifact checksum mismatch')
            target = safe_path(rel)
            if not target.is_file() or target.is_symlink():
                raise BridgeError('Missing or unsafe target asset')
            pending[rel] = raw
        # Stage both files before changing either. Roll back if any replacement
        # fails, including errors during the second replacement.
        staged = {}
        originals = {}
        replaced = []
        try:
            for rel, raw in pending.items():
                target = safe_path(rel)
                originals[rel] = target.read_bytes()
                temp = target.with_name('.bridge-' + uuid.uuid4().hex)
                staged[rel] = temp
                with temp.open('xb') as handle:
                    handle.write(raw)
                os.chmod(temp, 0o644)
            for rel in allowed:
                os.replace(staged[rel], safe_path(rel))
                replaced.append(rel)
        except Exception as exc:
            rollback_errors = []
            for rel in reversed(replaced):
                target = safe_path(rel)
                restore = target.with_name('.bridge-rollback-' + uuid.uuid4().hex)
                try:
                    restore.write_bytes(originals[rel])
                    os.chmod(restore, 0o644)
                    os.replace(restore, target)
                except Exception as rollback_exc:
                    rollback_errors.append(type(rollback_exc).__name__)
                finally:
                    restore.unlink(missing_ok=True)
            if rollback_errors:
                raise BridgeError('Asset import failed; rollback incomplete; inspect workspace before continuing') from exc
            raise BridgeError('Asset import failed; previous assets restored') from exc
        finally:
            for temp in staged.values():
                temp.unlink(missing_ok=True)
        audit('sync_frontend_assets', branch=active, job_id=job_id, paths=list(allowed))
        return {'branch': active, 'job_id': job_id, 'paths': list(allowed),
                'fingerprint': fingerprint(), 'requires_new_checks': True}

def commit_and_push(message: str) -> dict:
    if not 8 <= len(message) <= 160 or '\n' in message:
        raise BridgeError('Commit message must be 8-160 characters and single-line')
    with locked():
        active = validate_branch(branch())
        paths = changed_paths()
        if not paths:
            raise BridgeError('There are no changes to commit')
        current = fingerprint()
        second_check = {'crew_games': 'react_build',
                        'crew-dev-bridge': 'bridge_syntax',
                        'wedding': 'wedding_syntax'}[_ACTIVE_REPOSITORY.get()]
        if not _check_passed('pytest', current) or not _check_passed(second_check, current):
            raise BridgeError(
                'Both pytest and react_build must pass for the current changes'
                if _ACTIVE_REPOSITORY.get() == 'crew_games'
                else f'Both pytest and {second_check} must pass for the current changes'
            )
        local_sha = _git('rev-parse', 'HEAD')
        remote = github_request('GET', 'git/ref/heads/' + active)
        if remote['object']['sha'] != local_sha:
            raise BridgeError('Remote branch advanced; refresh workspace before committing')
        previous = github_request('GET', 'git/commits/' + local_sha)
        tree = []
        for path in paths:
            target = safe_path(path)
            if target.is_file():
                if target.is_symlink() or target.stat().st_size > max_file_size(path):
                    raise BridgeError('Unsafe or oversized changed file: ' + path)
                try:
                    content = target.read_text(encoding='utf-8')
                except UnicodeDecodeError as exc:
                    raise BridgeError('Binary files are not supported') from exc
                existing = _git('ls-files', '--stage', '--', path)
                mode = '100755' if existing.startswith('100755 ') else '100644'
                tree.append({'path': path, 'mode': mode, 'type': 'blob', 'content': content})
            else:
                tree.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': None})
        new_tree = github_request('POST', 'git/trees', {'base_tree': previous['tree']['sha'], 'tree': tree})
        commit = github_request('POST', 'git/commits', {'message': message, 'tree': new_tree['sha'], 'parents': [local_sha]})
        github_request('PATCH', 'git/refs/heads/' + active, {'sha': commit['sha'], 'force': False})
        _git('fetch', '--no-tags', 'origin', 'refs/heads/' + active, timeout=60)
        _git('reset', '--hard', 'FETCH_HEAD')
        # Ensure untracked files that were added in the commit don't linger.
        for path in paths:
            if not _git('ls-files', '--', path):
                target = safe_path(path)
                if target.is_file():
                    target.unlink()
        audit('push_commit', branch=active, commit=commit['sha'], paths=paths)
        return {'branch': active, 'commit_sha': commit['sha'], 'url': f'https://github.com/{_repo()}/commit/{commit["sha"]}'}


def open_draft_pr(title: str, description: str) -> dict:
    if not 6 <= len(title) <= 120 or len(description) > 12_000:
        raise BridgeError('Invalid PR title or description')
    active = validate_branch(branch())
    with locked():
        if changed_paths():
            raise BridgeError('Commit changes before opening a PR')
        pr = github_request('POST', 'pulls', {'title': title, 'body': description, 'head': active,
                                                'base': _base_branch(), 'draft': True, 'maintainer_can_modify': True})
        audit('create_pr', branch=active, pr_number=pr['number'])
        return {'number': pr['number'], 'url': pr['html_url'], 'draft': True}
