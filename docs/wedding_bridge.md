# Wedding V3 development bridge target

This change enables **development-only** access to `Prithvi7007/Wedding_Website_Final`,
with base branch `main`. It does not grant wedding production release,
PostgreSQL access, systemd control, or unrestricted VPS commands.

## Separation guarantees

- Allowed repository ID: `wedding`, fixed to `Prithvi7007/Wedding_Website_Final`.
- Separate `wedding-source/` checkout and `secrets/wedding_github_token` (never commit).
- Separate offline Python test environment in the runner.
- Wedding checks: `pytest` and `wedding_syntax`; both required before a GitHub push.
- Draft PRs target `main`, the canonical development and release branch.
- No changes to CREW release infrastructure, database, or systemd services.

## Controlled host activation

1. Review and merge this PR only after pytest, syntax tests, compose validation,
   and a release-workflow review.
2. Back up `/opt/crew-dev-bridge` configuration and directories.
3. `git fetch` / update the **bridge checkout** on the VPS with the merged commit.
4. Review `scripts/bootstrap.sh` before executing. It provisions an independent
   wedding-scoped PAT (Contents RW, Pull Requests RW, Metadata R). Never paste
   a PAT into ChatGPT or commit it to a repository.
5. Rebuild only the bridge/runner containers; do not restart `crew.service`
   or `wedding.service` or change production databases.
6. Verify `crew_repo_status(repository='wedding')`, run both check kinds on a
   disposable feature branch, open a draft PR, and verify isolation.

## Not yet implemented

The wedding **production** release agent must be independently designed and
installed with its own database backup/restore, staging rehearsal, CI gate,
service allowlist, non-destructive migrations and health/rollback checks.
A read-only `wedding_db` reporting role should be provisioned separately and
expose only approved aggregate/reporting views; no invitation tokens, personal
contact details, or guest-level notes are returned to ChatGPT by default.
