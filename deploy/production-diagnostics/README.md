# CREW read-only production diagnostics — operator review

**Status:** reviewable implementation; NOT installed on the VPS by creating this patch.

## Trust boundary

- The host-only `scripts/production_snapshot.py` collects only fixed, read-only observations. It runs as the existing non-root `crew` user, never receives arguments from ChatGPT, and does not need a GitHub or database token.
- It writes a small **allowlisted JSON snapshot** to `/var/lib/crew-dev-bridge/diagnostics/status.json`; this directory is owned by `crew`, group `10001`, **no group write**.
- Docker binds **only this directory** into the MCP bridge as `:ro`. The MCP tool `crew_production_health` reads and validates the JSON; it cannot run commands on the host, inspect `/opt/crew`, or query PostgreSQL.
- The snapshot excludes filenames, environment values, credentials, application logs, player data, network addresses, and raw subprocess output. The bridge discards unknown snapshot fields and rejects old snapshots (>15 minutes).
- `database_revision` intentionally reports `not_checked` until a separately designed PostgreSQL read-only role is approved. Do not infer DB migration status from deployed code version.
- The existing runner remains isolated; runner Docker settings and production units are not modified.

## Before applying

Review this patch and the permissions carefully. On the VPS, `cd /opt/crew-dev-bridge`, record the existing `git status --short` (the earlier runner ownership repair may still be uncommitted), and verify the patch with `git apply --check /path/to/crew-production-diagnostics.patch`. **Do not reset or overwrite local runner fixes.** Applying the patch modifies only development bridge code and its Compose definition; it does not deploy or restart production CREW.

## Host setup (operator-controlled, after patch review)

```bash
# Run from /opt/crew-dev-bridge after applying the patch.
# Group 10001 is the pre-existing bridge container's numeric group ID.
sudo install -d -o crew -g 10001 -m 2750 /var/lib/crew-dev-bridge/diagnostics

# Verify safe Python syntax and reader/unit tests before any bridge restart.
python3 -m py_compile bridge/production_status.py scripts/production_snapshot.py
python3 -m unittest discover -s tests -p 'test_production_diagnostics.py' -v

# Provision the unit and timer, but do not enable yet if you want to review them.
sudo install -m 0644 deploy/production-diagnostics/crew-production-snapshot.service /etc/systemd/system/
sudo install -m 0644 deploy/production-diagnostics/crew-production-snapshot.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Generate one snapshot safely and inspect metadata (no secrets are collected).
sudo systemctl start crew-production-snapshot.service
sudo systemctl status crew-production-snapshot.service --no-pager
sudo ls -ld /var/lib/crew-dev-bridge/diagnostics
sudo ls -l /var/lib/crew-dev-bridge/diagnostics/status.json
sudo cat /var/lib/crew-dev-bridge/diagnostics/status.json

# Validate Compose, rebuild and recreate the bridge container ONLY (not the runner).
docker compose config --quiet
docker compose up -d --no-deps --build bridge

docker compose ps
systemctl is-active crew nginx docker crew-tunnel

# If everything checks out, enable a fresh snapshot every five minutes.
sudo systemctl enable --now crew-production-snapshot.timer
```

MCP may need a refresh/reconnect to discover the new `crew_production_health` tool. It never accepts a command/path argument. If permission or filesystem checks fail, stop before recreating the bridge.

## Rollback (does not touch CREW production)

```bash
sudo systemctl disable --now crew-production-snapshot.timer
sudo systemctl stop crew-production-snapshot.service
```

Then revert only the reviewed bridge patch changes (respecting other local changes). Restore the bridge image via the reviewed previous commit and recreate only the bridge container. Do not run `git reset --hard`, `docker compose down`, or change the `crew` production unit.

## Known limitations

- The collector is deliberately read-only, not an infrastructure management interface.
- `git status` is observed without fetching remotes. Compare the SHA separately with GitHub's `main` to detect deployments lagging the repository.
- The health check uses the existing local CREW Unix socket and fixed `/health` endpoint; if the `crew` account cannot access the socket, `health_http_status` is `null` rather than guessing.
- Snapshot generation depends on the host's existing `crew` account and filesystem ownership. No production service configuration is changed.
