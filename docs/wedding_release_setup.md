# Wedding V3 — independent verified code-only release agent

**Review-only source. Do not install, enable, or start this agent before
completing the deployment preflight below.**

This adds a new, independently installed root-owned agent for the wedding
website. It never changes the CREW release agent, CREW service or crew_prod.

## Why code-only at first

Wedding V3 includes guest contacts, RSVPs and admin logs. Photo uploads are not yet implemented. Unlike
CREW's reviewed additive Alembic migration workflow, Wedding V3 includes
manual schema scripts. CI now exists on the protected `main` branch. This agent **refuses**
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

1. The live app is a **clean detached Git worktree** at
   `/var/www/wedding-v3-101f73e`. Its `.git` is a *file* pointing to
   `/var/www/wedding/.git/worktrees/wedding-v3-101f73e`; shared metadata
   lives under `/var/www/wedding/.git`. The agent requires these exact paths
   and detached HEAD. Do **not** check out `main` in the live worktree.
   The approved remote release branch is GitHub `main`; `origin` must
   equal `https://github.com/Prithvi7007/Wedding_Website_Final.git`.
2. Service `wedding.service` serves the running app from this checkout
   without changing its existing protected EnvironmentFile/credentials.
   Restarting ONLY this service must be safe.
3. Gunicorn serves `/readyz` on the local UNIX socket
   `/run/wedding/wedding.sock` with Host `adlinprithvi.cloud`.
   `/readyz` must return HTTP 200 and JSON `{"status":"ready"}`.
4. Photo uploads are **not implemented**. There must be no directory
   `/var/www/wedding-v3-101f73e/uploads` and zero rows in the existing
   `photo_uploads` database table. Both are checked on every release.
   Any uploaded photos, new upload/gallery feature files, or upload directory
   blocks automated releases until a new reviewed media-backup procedure ships.
   The guard does not discover arbitrary future storage paths.
5. PostgreSQL local peer auth permits a subprocess running as `postgres`
   to perform `pg_dump`, `createdb`, `pg_restore`, and `dropdb`.
   The fixed scratch database `wedding_release_verify` must not exist.
6. Writable, **dedicated** backup directory `/var/backups/wedding`,
   and root state dir `/var/lib/wedding-release` have private permissions.
   Verify disk capacity and retention separately.
7. The manually reviewed `deploy/wedding/wedding-ci.example.yml` has been
   committed to `Wedding_Website_Final/.github/workflows/wedding-ci.yml`
   via GitHub UI and completed successfully on an exact `main`
   push commit. The workflow name must be exactly **Wedding V3 CI**.
   The active ruleset protects `main`, requires pull requests and
   `wedding-quality` CI, and prevents branch deletion and force pushes.
8. The release agent must be installed outside any repository-writable
   directory, root-owned, with its own Unix socket. The bridge container
   must see only `/run/wedding-release` read-only. No Docker socket,
   database credential, app .env or other host command interface is mounted.

## Intended release sequence

1. GitHub branch head is obtained from the fixed public API, and an exact
   completed **successful push workflow** is mandatory; missing/failed/
   delayed/rate-limited/unavailable CI stops the release.
2. Confirm a clean detached worktree, exact Git metadata, and expected remote.
   Fetch the approved `main` SHA, require fast-forward history and code-only
   diffs; do not switch the live checkout to a branch.
3. Require existing app readiness and zero activity in the unfinished photo
   upload feature; then take a custom-format `pg_dump` of `wedding_db`
   with a SHA-256 manifest. No media archive is made until photo uploads ship.
4. Restore the backup to the uniquely named **scratch database only** and
   validate the `invitations` table before deleting scratch.
5. Recheck the target SHA and CI, then update the clean app checkout.
   Restart ONLY `wedding.service`, retry /readyz and verify the deployed SHA.
6. If health fails, attempt a **code-only** rollback to the previous commit
   and recheck readiness. Preserve new RSVPs and changes to `wedding_db`;
   NEVER auto-restore an old DB over current guest data. On a failed
   rollback, stop in `manual_recovery_required`.

All releases are triggered on demand. No polling loop auto-deploys a branch.

## Manual initial baseline alignment is mandatory

The live detached worktree is currently at
`9e44b6058a3673c388f75455cb2bb545ee55cb0f`. GitHub `main` now
contains the CI workflow and workflow-branch edit. Because these are
`.github/` changes, the code-only release agent intentionally **rejects**
this transition. An operator must separately review and back up the live
worktree and database, align the baseline without changing its detached
layout or production credentials, and verify readiness. Do not bypass change
gates or run an unreviewed `git reset --hard` against the live website.

The root-owned release service also needs write access to
`/var/www/wedding/.git` for Git objects, refs, and per-worktree metadata;
the service unit explicitly includes that directory. Review the permissions
and the scope of this host access before installation.

## Host installation after reviewed PRs and preflight

The agent is intentionally **not** installed by merging this PR.
The operator must separately review `host/wedding_release_agent.py`,
`deploy/wedding/wedding-release.service` and `compose.yaml`, create
private backup/state directories, install the root-owned agent and systemd
unit, reload systemd and enable/start the new service. Recreate only the
bridge container *after* creating the host socket directory; verify plan
is read-only and refuses release before CI exists. Always test a reversible,
code-only pilot release with operator observation before routine use.

## Remaining activation checks

- `Wedding V3 CI` is green on the protected `main` branch; check the exact target commit before any release.
- The wedding service and socket have been observed. Confirm the systemd
  unit, exact remote URL, metadata ownership and sandbox permissions on host.
- Photo uploads are unfinished; the automated release is deliberately blocked
  when known upload persistence or new photo/upload code appears.
- Do not bypass CI or missing preflight by changing environment flags.
- This release agent has no schema migration rights by design.

Do not share guest records, passwords, backup archives, private invitation
links or PostgreSQL DSNs in ChatGPT.
