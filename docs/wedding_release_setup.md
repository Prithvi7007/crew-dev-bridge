# Wedding V3 — independent verified code-only release agent

**Review-only source. Do not install, enable, or start this agent before
completing the deployment preflight below.**

This adds a new, independently installed root-owned agent for the wedding
website. It never changes the CREW release agent, CREW service or crew_prod.

## Why code-only at first

Wedding V3 includes guest contacts, RSVPs, admin logs and photos. Unlike
CREW's reviewed additive Alembic migration workflow, Wedding V3 includes
manual schema scripts and no established CI workflow. This agent **refuses**
changes under app/models, migrations, scripts, runtime/config files,
dependency files and GitHub workflows. Schema and dependency releases remain
a manually reviewed process.

The agent accepts only four fixed, no-argument calls:

- `wedding_release_capabilities`: fixed capabilities, no host secrets
- `wedding_release_plan`: current/target SHA and exact-commit CI verdict
- `wedding_release_execute`: request release only after all gates pass
- `wedding_release_status`: report final result or sanitized failure

No MCP API accepts a shell command, URL, arbitrary SHA, DB name, paths or
manual approval override.

## Required review and activation facts

Before installing, independently verify these *fixed* assumptions on the
VPS; if any are false, amend and retest the code in a NEW PR:

1. Wedding code: `/var/www/wedding-v3-101f73e`, checked-out branch
   `v3-production`, remote URL exactly
   `https://github.com/Prithvi7007/Wedding_Website_Final.git`.
2. Service `wedding.service` serves the running app from this checkout
   without changing its existing protected EnvironmentFile/credentials.
   Restarting ONLY this service must be safe.
3. Gunicorn serves `/readyz` on the local UNIX socket
   `/run/wedding/wedding.sock` with Host `adlinprithvi.cloud`.
   `/readyz` must return HTTP 200 and JSON `{"status":"ready"}`.
4. Guest uploads are in the existing, non-symlinked directory
   `/var/www/wedding-v3-101f73e/uploads`. **If uploads live elsewhere,
   fix the backup design before enabling releases.**
5. PostgreSQL local peer auth permits a subprocess running as `postgres`
   to perform `pg_dump`, `createdb`, `pg_restore`, and `dropdb`.
   The fixed scratch database `wedding_release_verify` must not exist.
6. Writable, **dedicated** backup directory `/var/backups/wedding`,
   and root state dir `/var/lib/wedding-release` have private permissions.
   Verify disk capacity and retention separately.
7. The manually reviewed `deploy/wedding/wedding-ci.example.yml` has been
   committed to `Wedding_Website_Final/.github/workflows/wedding-ci.yml`
   via GitHub UI and completed successfully on an exact `v3-production`
   push commit. The workflow name must be exactly **Wedding V3 CI**.
   Protect `v3-production` with required CI, reviews, and restricted pushes.
8. The release agent must be installed outside any repository-writable
   directory, root-owned, with its own Unix socket. The bridge container
   must see only `/run/wedding-release` read-only. No Docker socket,
   database credential, app .env or other host command interface is mounted.

## Intended release sequence

1. GitHub branch head is obtained from the fixed public API, and an exact
   completed **successful push workflow** is mandatory; missing/failed/
   delayed/rate-limited/unavailable CI stops the release.
2. Confirm clean `v3-production` checkout and exact expected remote;
   fetch the approved SHA, require fast-forward history and code-only diffs.
3. Require existing app readiness, archive guest uploads and take a
   custom-format `pg_dump` of `wedding_db` with SHA-256 manifest.
4. Restore the backup to the uniquely named **scratch database only** and
   validate the `invitations` table before deleting scratch.
5. Recheck the target SHA and CI, then update the clean app checkout.
   Restart ONLY `wedding.service`, retry /readyz and verify the deployed SHA.
6. If health fails, attempt a **code-only** rollback to the previous commit
   and recheck readiness. Preserve new RSVPs and changes to `wedding_db`;
   NEVER auto-restore an old DB over current guest data. On a failed
   rollback, stop in `manual_recovery_required`.

All releases are triggered on demand. No polling loop auto-deploys a branch.

## Host installation after reviewed PRs and preflight

The agent is intentionally **not** installed by merging this PR.
The operator must separately review `host/wedding_release_agent.py`,
`deploy/wedding/wedding-release.service` and `compose.yaml`, create
private backup/state directories, install the root-owned agent and systemd
unit, reload systemd and enable/start the new service. Recreate only the
bridge container *after* creating the host socket directory; verify plan
is read-only and refuses release before CI exists. Always test a reversible,
code-only pilot release with operator observation before routine use.

## Known blockers at initial submission

- `Wedding V3 CI` does not yet exist in `v3-production`; releases fail closed.
- Actual wedding service bind path and upload storage path have not been
  independently verified from the root-owned unit and filesystem.
- Do not bypass CI or missing preflight by changing environment flags.
- This release agent has no schema migration rights by design.

Do not share guest records, passwords, backup archives, private invitation
links or PostgreSQL DSNs in ChatGPT.
