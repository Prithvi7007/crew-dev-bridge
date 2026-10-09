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

import httpx

REPO = os.environ.get('CREW_REPO', 'Prithvi7007/crew_games')
WORKSPACE = Path(os.environ.get('CREW_WORKSPACE', '/workspace'))
QUEUE = Path(os.environ.get('CREW_QUEUE', '/queue'))
TOKEN_FILE = Path(os.environ.get('CREW_GITHUB_TOKEN_FILE', '/run/crew-secrets/github_token'))
BRANCH_RE = re.compile(r'^feature/[a-z0-9][a-z0-9._/-]{1,70}$')
MAX_FILE = 250_000
MAX_CHANGES = 30
EXCLUDED_PARTS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.ssh', '.aws', 'secrets'}
EXCLUDED_NAMES = {'.env', '.env.local', '.env.production', 'id_rsa', 'id_ed25519', '.npmrc', '.pypirc', '.netrc'}
LOCK = Path(os.environ.get('CREW_LOCK', '/queue/workspace.lock'))
AUDIT = Path(os.environ.get('CREW_AUDIT', '/audit/events.jsonl'))

class BridgeError(Exception):
    pass


def audit(event: str, **details) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({'timestamp': int(time.time()), 'event': event, **details}, sort_keys=True) + '\n'
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
    root = WORKSPACE.resolve()
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
    if not WORKSPACE.is_dir():
        raise BridgeError('Workspace missing: run bootstrap first')
    # Do not pass bridge credentials to a subprocess. Disable hooks and pagers.
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': '/tmp',
           'LANG': 'C.UTF-8', 'GIT_TERMINAL_PROMPT': '0',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0'}
    proc = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.pager=cat',
                           '-c', 'diff.external=', *args], cwd=WORKSPACE,
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
    digest.update(branch().encode())
    for p in changed_paths():
        target = safe_path(p)
        digest.update(p.encode() + b'\0')
        if target.is_file():
            if target.stat().st_size > MAX_FILE:
                raise BridgeError('Oversized file: ' + p)
            digest.update(target.read_bytes())
        else:
            digest.update(b'<deleted>')
    return digest.hexdigest()


def status() -> dict:
    return {'repository': REPO, 'branch': branch(), 'head': _git('rev-parse', 'HEAD'),
            'changes': changed_paths(), 'fingerprint': fingerprint(),
            'production_access': False}


def read_file(path: str) -> dict:
    target = safe_path(path, must_exist=True)
    raw = target.read_bytes()
    if len(raw) > MAX_FILE:
        raise BridgeError('File exceeds size limit')
    try:
        content = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise BridgeError('Only UTF-8 source files are available') from exc
    return {'path': path, 'sha256': hashlib.sha256(raw).hexdigest(), 'content': content}


def write_file(path: str, content: str, expected_sha256: str) -> dict:
    if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_FILE:
        raise BridgeError('Only UTF-8 text files up to 250 KB are accepted')
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
    if not TOKEN_FILE.is_file():
        raise BridgeError('GitHub credential not provisioned: add repository-scoped token')
    token = TOKEN_FILE.read_text().strip()
    if not token:
        raise BridgeError('GitHub credential is empty')
    # Endpoint supplied only by server-owned code, never user input.
    url = 'https://api.github.com/repos/' + REPO + '/' + endpoint.lstrip('/')
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
        main = github_request('GET', 'git/ref/heads/main')
        sha = main['object']['sha']
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
    shutil.copytree(WORKSPACE, target, ignore=ignore, symlinks=False)


def start_check(kind: str) -> dict:
    if kind not in ('pytest', 'react_build'):
        raise BridgeError('Only pytest or react_build are supported')
    with locked():
        validate_branch(branch())
        job_id = uuid.uuid4().hex
        job_dir = QUEUE / 'snapshots' / job_id
        (QUEUE / 'jobs').mkdir(parents=True, exist_ok=True)
        (QUEUE / 'results').mkdir(parents=True, exist_ok=True)
        _snapshot(job_dir)
        ticket = {'job_id': job_id, 'kind': kind, 'fingerprint': fingerprint(), 'branch': branch(),
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
        return {k: data.get(k) for k in ('job_id', 'kind', 'state', 'exit_code', 'output', 'fingerprint', 'branch')}
    if (QUEUE / 'jobs' / (job_id + '.json')).is_file():
        return {'job_id': job_id, 'state': 'queued_or_running'}
    raise BridgeError('Unknown check id')


def _check_passed(kind: str, current: str) -> bool:
    results = QUEUE / 'results'
    if not results.is_dir():
        return False
    for result in list(results.glob('*.json'))[-100:]:
        try:
            data = json.loads(result.read_text())
            if data.get('kind') == kind and data.get('state') == 'passed' and data.get('fingerprint') == current:
                return True
        except (OSError, ValueError):
            continue
    return False


def commit_and_push(message: str) -> dict:
    if not 8 <= len(message) <= 160 or '\n' in message:
        raise BridgeError('Commit message must be 8-160 characters and single-line')
    with locked():
        active = validate_branch(branch())
        paths = changed_paths()
        if not paths:
            raise BridgeError('There are no changes to commit')
        current = fingerprint()
        if not _check_passed('pytest', current) or not _check_passed('react_build', current):
            raise BridgeError('Both pytest and react_build must pass for the current changes')
        local_sha = _git('rev-parse', 'HEAD')
        remote = github_request('GET', 'git/ref/heads/' + active)
        if remote['object']['sha'] != local_sha:
            raise BridgeError('Remote branch advanced; refresh workspace before committing')
        previous = github_request('GET', 'git/commits/' + local_sha)
        tree = []
        for path in paths:
            target = safe_path(path)
            if target.is_file():
                if target.is_symlink() or target.stat().st_size > MAX_FILE:
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
        return {'branch': active, 'commit_sha': commit['sha'], 'url': f'https://github.com/{REPO}/commit/{commit["sha"]}'}


def open_draft_pr(title: str, description: str) -> dict:
    if not 6 <= len(title) <= 120 or len(description) > 12_000:
        raise BridgeError('Invalid PR title or description')
    active = validate_branch(branch())
    with locked():
        if changed_paths():
            raise BridgeError('Commit changes before opening a PR')
        pr = github_request('POST', 'pulls', {'title': title, 'body': description, 'head': active,
                                                'base': 'main', 'draft': True, 'maintainer_can_modify': True})
        audit('create_pr', branch=active, pr_number=pr['number'])
        return {'number': pr['number'], 'url': pr['html_url'], 'draft': True}
