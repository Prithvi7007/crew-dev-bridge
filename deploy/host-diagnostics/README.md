# Restricted host diagnostics — optional deployment

**Status:** source and tests only. Nothing is installed or enabled on the VPS merely
by merging this PR. The existing 30-tool bridge continues to work if the agent
socket is absent; `crew_db_query` and `crew_vps_exec` then return a safe error.

## Architecture

`ChatGPT → private MCP bridge (UID 10001) → /run/crew-host-diag/host.sock
→ unprivileged host agent (User=crew; Group=986) → constrained diagnostics`

The host daemon authenticates the connecting process through Linux
`SO_PEERCRED`. The Docker bridge gets only a **read-only mount of the socket**;
no PostgreSQL password, Docker socket, root access, shell, or full host mount.
The daemon supports two operations:

- **`crew_vps_exec(command, args)`** uses a fixed `argv` mapping without
  `shell=True`. Supported commands: `uptime` (no args), `memory` (no args),
  `disk` (zero args or exactly one approved path: `/`, `/opt/crew`,
  `/opt/crew-dev-bridge`), `service` (one explicitly approved unit),
  `git_revision` (one of `crew_games` or `crew-dev-bridge`). No restarts,
  writes, arbitrary filenames, `sudo`, secrets or interactive commands.
- **`crew_db_query(sql, params, limit)`** accepts a *single SELECT*, no
  comments or extra statements, with parameters and at most 100 returned rows.
  The agent starts a PostgreSQL **READ ONLY transaction**, applies 4-second
  query / 1-second lock timeouts, and limits returned cells and bytes.
  **SQL-string validation is not a security boundary**: the database's
  separately provisioned reporting-only role and controlled views are essential.

All requests have bounded transport sizes, output, timeouts and concurrency.
Only operation names, success/failure and durations enter the daemon audit log;
query text, parameter values and passwords are never logged. SQL errors are
reported as `reporting_query_failed`, not returned verbatim.

## Required human-approved host setup

Do **not** activate the service until the following conditions are true:

1. Verify that the Linux user `crew`, UID 10001 for the bridge container,
   and host group GID **986** are the intended, trusted identities on this VPS.
   Check with `id crew`, `getent group 986` and `docker compose config`.
   The service deliberately runs without root privileges.
2. Configure a PostgreSQL login role such as `crew_reporting_reader` with
   **no SUPERUSER, CREATEDB, CREATEROLE, BYPASSRLS, pg_read_all_data,
   pg_read_server_files, or write privileges**. Provision a dedicated schema
   `crew_reporting` containing vetted, minimally disclosed reporting views
   for rankings/score diagnostics. Grant only `CONNECT`, `USAGE` on the
   reporting schema, and `SELECT` on explicitly approved views. Do **not**
   grant `SELECT` on `public.users`, raw PII, or production base tables.
   Set the role's default transactions to read-only. Review views for
   SECURITY DEFINER functions or indirectly exposed sensitive data.
   The example `crew_reporting.weekly_scores` appearing in tests is **not**
   a claim that such a view already exists.
3. Store a restricted DSN for that login in a **root-owned, mode 0600** file
   `/etc/crew-host-diag/db_dsn`; parent directory mode 0700. Prefer a
   local Unix PostgreSQL socket, or restrict a local TCP listener to loopback.
   Do not add this DSN to Git, Docker mounts, environment variables or MCP
   tool arguments. systemd `LoadCredential=` transfers it to the service.
4. Create an independent host-only Python venv at
   `/opt/crew-dev-bridge/.host-agent-venv` and install
   `psycopg[binary]==3.2.10`. Do not reuse production application's venv.
5. After an operator reviews the merged version, install
   `deploy/host-diagnostics/crew-host-diag.service` into the host systemd
   units, `systemctl daemon-reload`, then enable/start the service.
   Review `systemctl status crew-host-diag.service` and confirm its socket
   owner is `crew`, group GID 986, mode 0660. systemd creates
   `/run/crew-host-diag` using `RuntimeDirectoryMode=0770`.
6. Only then rebuild/recreate **the bridge container** with the new
   read-only `/run/crew-host-diag` socket bind mount. Verify existing 30 tools
   and the two new tools. Never restart `crew.service` for this feature.

If a deployment prerequisite is missing, **stop** and fix it without disabling
the security restrictions. Host setup is separate from PR merge/approval.

## Verification

In the offline runner: `crew_start_check(kind="pytest", repository="crew-dev-bridge")`
and `crew_start_check(kind="bridge_syntax", repository="crew-dev-bridge")`.

After approved host deployment, verify the gateway first with
`crew_vps_exec(command="uptime")`, then
`crew_vps_exec(command="service", args=["crew.service"])`. Database testing
should use a non-sensitive query against a previously approved reporting view.

Rollback: stop/disable `crew-host-diag.service`, roll back the bridge image
to its prior source, and leave the production CREW application untouched.
