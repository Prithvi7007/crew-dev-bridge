# Wedding V3 — isolated database diagnostics (operator review required)

This change adds a **separate** wedding-only, **aggregate-only** reporting
socket. It does not modify CREW reporting or install anything on the VPS.

## Scope

MCP tool: `wedding_db_report(report='overview'|'attendance'|'health')`.

- `overview`: total invitations, events, RSVPs, uploaded-photo records and admin
  change requests, without guest names, invitation tokens, contact details or notes.
- `attendance`: only global Yes/No/Maybe and confirmed-guest counts.
- `health`: returns a fixed "ready" result if the reporting view responds.

The MCP bridge never opens a PostgreSQL connection and sends only a fixed
report identifier to the host. The host uses an independent Linux service with
peer UID validation, hardcoded read-only SQL, short timeouts and a separately
privileged DB user with SELECT on only three aggregate views. It does not
accept arbitrary SQL, raw table queries, filenames or commands.

## Provisioning, after reviewing the PR and database schema

**Never perform setup while a production deployment is running.** Before
touching permissions, snapshot the wedding database and check role grants.
All commands below are human-initiated on Hostinger Web Console, not MCP calls.

1. Create a PostgreSQL login role named `wedding_reporter`, with NOINHERIT,
   NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION and an independently
   generated strong password. Use PostgreSQL's interactive `\\password
   wedding_reporter` so it is not displayed or entered in shell history.
   Confirm the role has no membership in a table-owning/writing role.
2. Run `sudo -u postgres psql -v ON_ERROR_STOP=1 -d wedding_db
   -f deploy/wedding/reporting.sql`. Review the script before doing so.
   The script creates ONLY `wedding_reporting` aggregate views, with no
   INSERT/UPDATE/DELETE/DDL permissions for `wedding_reporter`.
3. Confirm the new role has SELECT only on the three reporting views and
   **no direct SELECT on public invitations, RSVPs, photo records, or admin
   records**. Test using that account before enabling the service.
4. Create a distinct unprivileged Linux account `weddingdiag` with no shell,
   home or sudo. Create `/etc/wedding-diagnostics/report_dsn` as `root:root
   0600` containing a PostgreSQL libpq DSN for `wedding_db` and that
   account alone. **Never paste the DSN or password into ChatGPT**.
   The systemd `LoadCredential=` setting makes it available only to this service.
5. Create an isolated Python venv (e.g. `/opt/wedding-diagnostics-venv`)
   with reviewed `psycopg[binary]` dependencies. The reviewed service unit
   expects this executable; keep it distinct from the CREW and wedding app
   runtime environments.
6. Install `host/wedding_diagnostics.py` to
   `/usr/local/lib/wedding-diagnostics/agent.py` (root-owned 0644), and
   `deploy/wedding/wedding-diagnostics.service` to
   `/etc/systemd/system/wedding-diagnostics.service`.
   Confirm bridge socket group `986` exists; review systemd sandboxing.
7. Start ONLY `wedding-diagnostics.service`, verify its socket at
   `/run/wedding-diagnostics/diag.sock`, and independently test
   `overview`, `attendance`, and `health`.
8. Only after the socket directory exists, rebuild/recreate **only** the
   bridge container to install the new MCP tool and mount the socket read-only.
   Avoid restarting `crew.service`, `wedding.service`, databases or
   the CREW release agent.

If the service is not provisioned, the new tool returns
`wedding_diagnostics_unavailable`, not an invented result.

### Rollback

Stop/disable only `wedding-diagnostics.service` and restore the previously
reviewed bridge container image if needed. The new PostgreSQL role/views
are not part of a schema migration; retain them for operator cleanup if
rollback is necessary. **Do not remove anything from the `public` schema**.

## Separate production release work

This PR does not add wedding deployment rights. The wedding release agent
requires GitHub CI on `v3-production`, exact service/runtime configuration,
code + DB + uploads backup planning, verified staging/rehearsal and code-only
rollback. Schema changes require separate manual approval.
