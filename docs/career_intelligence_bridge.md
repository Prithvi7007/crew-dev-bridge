# Career Intelligence bridge target

This integration adds **development-only** access to
`Prithvi7007/Career-intelligence` using the existing CREW Development Bridge.
It does not add VPS deployment, public web access, database access,
application submissions, or arbitrary shell commands.

## Repository and isolation

- Tool repository selector: `career-intelligence` (case-sensitive).
- Fixed GitHub repo: `Prithvi7007/Career-intelligence`.
- Base branch: `main`. All source changes go through `feature/*` + reviewable draft PRs.
- Independent source checkout: `/opt/crew-dev-bridge/career-source` on the VPS,
  bind-mounted as `/career-source` in the bridge container.
- Independent fine-grained GitHub token: `secrets/career_github_token`,
  exposed only inside the bridge container (never to the offline runner).
- Offline checks, **both** required for the exact source fingerprint:
  `career_test` runs `node --test`, and `career_syntax` runs
  `node --check server.mjs`.
- Existing CREW, bridge, and wedding repository permissions and check policies
  remain unchanged.

## One-time host activation (operator)

1. Initialize `Prithvi7007/Career-intelligence` with a README on its
   `main` branch in GitHub if it is still empty. The bridge intentionally
   cannot write directly to `main`.
2. Review and merge the bridge feature PR only after `pytest`,
   `bridge_syntax`, and existing CI checks pass.
3. Back up `/opt/crew-dev-bridge` runtime configuration and confirm the current
   production CREW/wedding services are healthy.
4. Create a **fine-grained PAT scoped only to Career-intelligence** with
   Contents read/write, Pull requests read/write, Metadata read. Enter it
   using the VPS's hidden terminal prompt when running bootstrap; do not put
   credentials in ChatGPT, GitHub, command arguments, or tracked source files.
5. Update only the `/opt/crew-dev-bridge` source checkout to the merged commit.
   Review `scripts/bootstrap.sh`, then invoke it during a planned bridge
   maintenance window. It clones the Career Intelligence repo and provisions
   a separate token file. This rebuilds the bridge and the **offline runner**
   containers; it does not restart `crew.service` or `wedding.service`.
   If needed, inspect the same Docker setup already used by the bridge.
6. Confirm `crew_repo_status(repository='career-intelligence')`, create a
   disposable `feature/*` branch, and verify that both `career_test`
   and `career_syntax` run successfully.
7. Verify the existing `crew_games`, `crew-dev-bridge`, and `wedding`
   selectors still resolve, and that no production services were altered.

## Starter compatibility

The Career Intelligence starter uses Node.js 22+ and ships
`server.mjs` plus built-in Node test files in `test/`. It can run
without dependency installation in the offline bridge test runner.
The plugin does not install Career-Ops itself or start the web app.

## Deployment boundary

Merging this bridge PR **does not activate the new repository on the VPS**.
A host operator must perform the one-time setup and validate the updated
bridge/runner containers. A separate, reviewed Career Intelligence deployment
workflow will be required before the dashboard can be made accessible.
