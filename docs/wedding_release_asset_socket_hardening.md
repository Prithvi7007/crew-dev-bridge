# Wedding V3: asset smoke verification and stable MCP socket

This source update is **not** automatically installed on the VPS. The
independently installed agent and systemd unit must be operator-reviewed,
backed up, installed, and restarted separately. It does not deploy Wedding.

## Fixes

- Verify via real local Nginx HTTPS/SNI that public CSS, JS, favicon, and a
  slideshow image return byte-for-byte contents matching the deployed
  worktree. Fail closed for 403, 404, missing assets, TLS errors, stale bytes.
- Probe before backing up a new release, after the Wedding service restarts,
  and after any code rollback. Gunicorn `/readyz` alone cannot detect static
  asset delivery failures.
- Preserve `/run/wedding-release` with
  `RuntimeDirectoryPreserve=yes` so the already-mounted read-only bridge
  container does not retain an obsolete directory inode across manual service
  restarts. The agent itself safely replaces its existing Unix socket.

## Operator activation (after PR merge)

1. Compare exact GitHub merged main commit, ensure clean VPS source, and pull
   `/opt/crew-dev-bridge` only. Review the new fixed HTTPS probe before
   installation; it contacts local Nginx on `127.0.0.1:443` with a validated
   `adlinprithvi.cloud` certificate and **no** invitation tokens.
2. Back up both
   `/usr/local/lib/wedding-release/release_agent.py` and
   `/etc/systemd/system/wedding-release.service` in a private operator
   directory. Verify installed agent and unit correspond to reviewed source.
3. Install the reviewed Python agent and systemd unit as root-owned files.
   Run `systemctl daemon-reload`, then restart **only**
   `wedding-release.service`. Do not restart `wedding.service`.
4. The first restart when enabling preservation may change the directory
   inode; if MCP becomes unavailable, recreate **only** the Docker bridge
   container once to attach the current directory.
5. Verify the directory inode is unchanged by a subsequent
   `systemctl restart wedding-release.service`, and ensure the existing
   bridge bind mount still sees the Unix socket.
6. Verify MCP capabilities/plan/status all return `ok:true`. Before any
   deployment, explicitly inspect six public assets using Nginx HTTP 200
   and content SHA-256. Confirm clean Wedding production SHA and app
   readiness; no deployment should be initiated as part of installation.

Production code-only releases still require exact green CI and backup/scratch
rehearsal. Schema/workflow/dependency changes still require manual review;
never automatically restore a database over newer guest RSVPs.
