# CREW Development Bridge — Version 0.2 (private pilot)

A single-repository, **private MCP development bridge** for `Prithvi7007/crew_games`.
This is a standalone service. It does **not** modify or mount the production `/opt/crew` app.

> **Status:** Source, static checks, and unit tests validated locally. Docker image builds,
> real GitHub write operations, MCP handshake, and ChatGPT connection **must be verified
> on your VPS before enabling write tools**. Do not treat this as a security audit.

## Architecture

```
ChatGPT → Secure MCP Tunnel → 127.0.0.1:8777 on VPS
                                    |
                  MCP Bridge container (GitHub-scoped credential)
                   |             |                 |
              /source       GitHub API         /queue jobs
              git tree     feature branches          |
                     commit + draft PR       Runner container
                                              - no network
                                              - no GitHub credential
                                              - no production mounts
                                              - read-only root filesystem
                                              - pytest / React build only
```

Never expose the bridge port on a public interface. The Docker Compose binds it to
`127.0.0.1` on the host. Use OpenAI's **Secure MCP Tunnel** rather than opening the
port directly. The tunnel provides a private connection; restrict which ChatGPT
workspace/users can use it. For a multi-user or public service, add proper OAuth 2.1
resource-server authorization before use.

## Tools (10)

| MCP tool | What it does |
|---|---|
| `crew_repo_status` | Inspect checkout, active branch, and changed files |
| `crew_read_file` | Read UTF-8 files with optimistic SHA-256 edit token |
| `crew_diff` | View bounded diff and untracked paths |
| `crew_create_branch` | Create new `feature/*` branch from `main` |
| `crew_write_file` | Write a UTF-8 file on a feature branch |
| `crew_apply_patch` | Validate and apply a bounded unified patch |
| `crew_start_check` | Queue `pytest` or `react_build` in isolated worker |
| `crew_check_status` | Read result of a queued check |
| `crew_commit_and_push` | Create GitHub commit only after BOTH checks pass |
| `crew_open_draft_pr` | Open draft PR into main; never merges |

### Restrictions

- Fixed repo only: `Prithvi7007/crew_games`; fixed base branch: `main`.
- All writes require an active `feature/*` branch.
- Never merges PRs or deploys production.
- No arbitrary shell command execution or arbitrary network destinations.
- Only UTF-8 files up to 250 KB; patch max 125 KB; at most 30 changed files.
- No symlinks, binaries, `.env`, secrets, or `.github/workflows` edits.
- Source is copied into a snapshot before checks; runner doesn't receive a GitHub token.
- Both offline pytest and Vite build must pass on the **exact current working-tree fingerprint** before pushing.
- Important actions are auditable at `audit/events.jsonl` (gateway-only mount).
- The container isolation is defense in depth, **not a replacement for VM isolation and CI review**.

## Setup (VPS operator)

### 1. Prerequisites

- A VPS with Docker Engine and Docker Compose v2, sufficient resources for a 2 GiB test worker.
- A working `git` command. Do not reconfigure/restart the production Flask service.
- A **fine-grained GitHub personal access token** restricted to repository `Prithvi7007/crew_games`:
  - Metadata: read (mandatory default)
  - Contents: read and write
  - Pull requests: read and write
- Access to [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
  and permission to create a custom MCP app in your ChatGPT workspace.
- **Never paste tokens into ChatGPT**, shell command history, or a git repository.

### 2. Unpack separately from production

Copy the ZIP to your VPS using your normal SCP/SFTP tool, then:

```bash
sudo mkdir -p /opt/crew-dev-bridge
sudo unzip -o /tmp/crew-dev-bridge-v0.1.zip -d /opt/crew-dev-bridge
cd /opt/crew-dev-bridge
```

Replace `/tmp/crew-dev-bridge-v0.1.zip` with the actual uploaded ZIP path.

### 3. Bootstrap

**Review `compose.yaml`, `bridge/core.py`, and `scripts/bootstrap.sh` before running.**

```bash
cd /opt/crew-dev-bridge
bash scripts/bootstrap.sh
sudo docker compose ps
sudo docker compose logs --tail=80 bridge
sudo docker compose logs --tail=80 runner
```

The bootstrap script prompts securely (no echo) for the token on first run,
clones the public repository into `source/`, builds the isolated test runner,
and starts the two containers. It configures group-private queue folders and an isolated UID for repository-controlled test processes. It creates `secrets/` with restricted filesystem
permissions. If your Docker user already has access to the daemon, `sudo` may
not be necessary for Compose, but use one consistent Docker context.

**Do not run the bootstrap if your VPS is short on disk/memory or if you have
not taken a VPS snapshot/backup.** Docker installation itself is outside the
scope of this package. Do not blindly install a new Docker daemon on a
production host without checking its effect on firewall and existing services.

### 4. Verify private local MCP endpoint

From the VPS, confirm that only localhost is published:

```bash
sudo docker compose ps
ss -lnt | grep 8777
```

Then issue an MCP handshake:

```bash
curl -sS --max-time 15 -X POST http://127.0.0.1:8777/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"crew-smoke","version":"0.1"}}}'
```

Expected: a JSON-RPC initialize response advertising an MCP server. An MCP
SDK version mismatch or server error is a stop condition—don't open the tunnel.

### 5. Connect ChatGPT through the secure tunnel

Follow the [official Secure MCP Tunnel instructions](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).
Point the tunnel's local destination to `http://127.0.0.1:8777/mcp` and keep
its runtime credentials in the tunnel process only. Register the tunnel as a
custom MCP app in ChatGPT, inspect the listed tools, and restrict who can call
write actions. **Do not make port 8777 public.**

ChatGPT account/workspace entitlement matters: full write-enabled custom MCP
apps may not be available on every plan or workspace. A working VPS server
alone does not guarantee this ChatGPT conversation can invoke it.

### 6. Dry-run the first CREW feature

1. Invoke `crew_repo_status` and confirm branch `main`, clean tree.
2. Create `feature/season-badges-foundation`.
3. Use `crew_apply_patch` with the existing approved badge foundation patch.
4. Start `pytest` and `react_build`, polling each check's ID for results.
5. Inspect `crew_diff` and `audit/events.jsonl`; resolve any failing tests.
6. Call `crew_commit_and_push` only when BOTH checks pass.
7. Create a **draft** PR for human review and wait for GitHub CI before merge.

## Troubleshooting / rollback

```bash
cd /opt/crew-dev-bridge
sudo docker compose logs --tail=150 bridge runner
sudo docker compose down
```

`docker compose down` stops the bridge without affecting `/opt/crew`, and
retains `source/`, `queue/`, `audit/`, and `secrets/`. To rotate the GitHub token,
update `secrets/github_token` outside any tracked source and restart the bridge.
Revoke the token in GitHub immediately if compromised.

Don't remove `source/` while a check is running. The bridge intentionally has no
PR merge/deployment tools; deploy CREW through your existing controlled workflow.

## Known v0.2 limitations

- The gateway uses a private tunnel and workspace controls rather than its own
  user-level OAuth 2.1 authorization; keep this **single-admin/private**.
- The test worker uses Docker container containment, not a separate virtual machine. Its small supervisor runs as root with only CHOWN/SETUID/SETGID inside the container in order to drop each test subprocess to UID 10002. The development source and tests remain untrusted.
- The bridge maintains one active feature branch at a time, not parallel workspaces.
- `runner/Dockerfile` locks Python requirements to the repo file and npm to the
  repo lockfile **at image build time**. Rebuild it after dependency updates.
- GitHub push API and MCP handshake haven't been verified here against live
  credentials; test them on a disposable feature branch before real changes.
- Results aren't cryptographically attested: always review GitHub CI and the
  patch before merging. Never allow unreviewed code to reach production.
