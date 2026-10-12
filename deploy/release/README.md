# Reusable CREW release bridge (v1)

This replaces the *release-specific*, SHA-pinned operational dead ends with four stable tools. They deploy the **latest merged GitHub main** without a per-release code patch.

| Tool | Result |
|---|---|
| `crew_release_capabilities()` | Confirms host agent installation and supported operations |
| `crew_release_plan()` | Reads current production SHA, merged main SHA, DB revision, and GitHub CI gate |
| `crew_release_execute()` | Queues verified backup, scratch rehearsal, additive DB migration, deploy and health check |
| `crew_release_status()` | Polls job step, outcome and sanitized failure; no fake success |

## Security model

The MCP Docker bridge remains unprivileged (UID 10001) and connects only through a read-only-mounted UNIX socket directory at `/run/crew-release`. Host worker accepts only four **zero-argument** operations, requires Linux `SO_PEERCRED` for bridge UID 10001, and is installed as **root-owned immutable code outside the crew-writable checkout**. It accepts no caller-supplied shell, arbitrary argv, repository URL, commit SHA, paths, DB name or approval flags.

`crew_release_execute` picks the public GitHub `main` tip from the **pinned CREW repository only**, requires the expected GitHub Actions **test-and-audit** check to have completed successfully on that exact commit, refuses a dirty production checkout, ensures fast-forward history, and stages a detached worktree.

It uses only the configured CREW PostgreSQL database `crew_prod` via the root-owned `/etc/crew/crew.env`, creates a fresh custom-format backup and SHA-256 checksum, restores into the exclusively named, non-existing scratch DB `crew_release_verify`, rehearses migrations as unprivileged `crew`, verifies preserved record counts, then applies additive production migrations. Only then does it update the production checkout and restart `crew.service`. It verifies `/health/ready` and the deployed SHA. On a post-update failure, it attempts **code-only** rollback, preserving the migrated database and the backup. It never restores an older DB over recent gameplay.

The root-owned release service grants only the pre-existing `CAP_SETUID` and `CAP_SETGID` capabilities in the ambient set so the Python worker can drop child process identity even with `NoNewPrivileges=true`; those capabilities remain within the existing restricted bounding set. The existing `crew_release_capabilities` response exposes only whether those two capabilities are effective, to help diagnose host-level misconfiguration without leaking raw process information.

The worker drops subprocess credentials directly to the fixed `crew` or `postgres` account using Python's subprocess UID/GID/group controls. It does not use PAM or the `runuser` helper, and it never launches an untrusted shell. The systemd service retains `NoNewPrivileges=true` and its restricted capability bounding set.

The installer creates /var/lib/crew-release as `root:986`, matching the root-owned systemd service's `Group=986`. Matching ownership prevents systemd's `StateDirectory=` management from recursively taking ownership of the separate `crew`-owned staging directory on restart. The installer verifies ownership and `crew` staging write/traverse access **after** restarting the agent, failing installation if either is wrong. No broad permission changes or recursive chown of the app are used.

The read-only release plan now verifies that the fixed production Git metadata, Git worktree registry and release staging parent are writable and traversable as the unprivileged `crew` identity. The installer repairs only the dedicated Git worktree registry directory (not the repository or database) before restarting the agent. Existing or unexpected staging candidates fail closed for operator review.

The first installation is a privileged **human-reviewed trust establishment**. After that, normal future *additive* releases require no new hardcoded SHA or release-specific tool. Destructive/custom migrations or interrupted releases are intentionally stopped for operator review. The bridge cannot safely install its own root agent—doing so would defeat the trust boundary.

## One-time VPS installation (after this bridge PR is merged)

As the VPS operator, review `host/release_agent.py`, `deploy/release/install.sh`, `deploy/release/crew-release.service` and `compose.yaml` in the PR. Confirm bridge socket group GID 986 and that Linux user `crew` exists. Confirm your production repository, database, and environment paths match the CREW installation specified in the worker.

Then:

```bash
cd /opt/crew-dev-bridge
git fetch origin main
git checkout main
git pull --ff-only origin main
sudo bash deploy/release/install.sh
```

The installer copies the reviewed Python agent to `/usr/local/lib/crew-release/release_agent.py` owned by root, prepares the controlled worktree and backup directories, installs and starts `crew-release.service`, and rebuilds **only the bridge** container to expose the new four MCP tools. It does not deploy CREW or migrate the DB. Existing diagnostic and development tools remain available.

**Do not** copy or source credentials, grant Docker socket or PostgreSQL superuser access to the MCP bridge, or open the UNIX socket on TCP.

## First acceptance release: PR #41

With the agent installed:

1. `crew_release_capabilities()` must report `reusable_v1`.
2. `crew_release_plan()` should report the merged main SHA (PR #41 merge `02a81f1...` if still current), current production commit and `v15_badges` DB revision; CI must be successful.
3. After reviewing the plan, run `crew_release_execute()` and poll `crew_release_status()`. A queued job **is not** success.
4. Expect final `state: passed`, `database_revision: v16_trophy_tour`, production SHA = verified main.
5. Independently check `crew_maint_dashboard()`, login and staff preview: `https://cadacrew.fun/trophies?trophy_tour_preview=1`. The Oct 19 public reveal gate remains unchanged.

If the worker returns `non_additive_migration_requires_operator_review`, `quality_gate_not_green`, `stale_release_worktree`, `release_already_running_or_interrupted`, or `manual_recovery_required`, **stop and inspect host service logs**. Never work around a failed gate by changing the supplied SHA or invoking a shell through MCP.

## Public React asset permissions and readiness

The host release worker retains its restrictive process-wide umask (077) for
state and backup bookkeeping, but Git checkout/reset subprocesses now use umask
022. After a verified main checkout, the worker checks exactly two committed
public React files, `player.js` and `player.css`, through no-symlink file
descriptors and normalizes them to mode 0644 with group `www-data`, while the
public React directory is 0755. This also corrects existing assets created by
earlier failed releases, and the same repair applies during a code rollback.
Unexpected owner, file type, missing/empty asset or symlink stops the release.

After restarting the app, the worker retries the fixed local UNIX-socket
`/health/ready` probe for a bounded startup period before considering rollback.
Application success requires final release state `passed` plus independent
production commit and database verification. Never assume a queued release
succeeded.

## Validation and rollback

Offline tests: `crew_start_check(kind="pytest", repository="crew-dev-bridge")` and `crew_start_check(kind="bridge_syntax", repository="crew-dev-bridge")`. The tests mock the host and do not touch production.

For an agent malfunction: `sudo systemctl disable --now crew-release.service` on the host. Do not delete DB backups or downgrade Alembic. The prior CREW production code remains available; the runner attempts code rollback if the health check fails. The agent intentionally does not automatically recover from an interrupted root service while a deployment was in progress.

### Limits

- Schema safety permits additive migrations only; migrations containing destructive or raw SQL primitives require a separate operator-reviewed procedure.
- Hardcoded **identity and location of the single CREW app and its database** are intentional scope boundaries, not per-release version pins.
- A successful test suite is not a full production rehearsal; the first live run must be observed by the operator and production health independently verified.
- CI retrieval uses the public GitHub API and fails closed when unavailable or rate-limited.
