# Motion for React: secure offline test runner

CREW Rewind 2.0 is adopting the public, MIT-licensed `motion` package.
The `crew-dev-bridge` runner uses `network_mode: none` at test time and
symlinks the trusted, image-baked `/opt/frontend/node_modules`. Do not
enable `npm install` or `npm ci` in the untrusted test worker.

## Phase A: prepare the trusted runner (this PR)

The runner Dockerfile installs **exactly motion@14.6.0** during its trusted
container image build using `--ignore-scripts --no-save --no-package-lock`.
No npm registry access, installation command, or network access is added to
the live test worker. A regression test protects these boundaries.

**Important:** Merging this PR does not rebuild the VPS image. The VPS
operator must rebuild `crew-dev-runner:local` from a pinned, verified
`crew_games` checkout and recreate the runner service using the bridge's
existing reviewed Docker Compose procedure. Ensure the runner starts and
passes its existing checks before moving to Phase B.

## Phase B: add the app dependency

A separate `crew_games` PR should:
1. Add the same exact `motion@14.6.0` dependency to
   `frontend/package.json` and regenerate `frontend/package-lock.json`
   with a trusted networked npm installation.
2. Verify that GitHub CI's existing `npm ci` and `npm run build` pass.
3. Integrate `AnimatePresence`, `motion` components and reduced-motion
   controls **only in Rewind**, leaving site-wide native motion unchanged.
4. Commit the reproducible built `player.js` and `player.css`, test in
   the isolated runner, and obtain normal PR approval before release.

The dependency version must be kept in sync in both repos until the next
planned runner-image dependency refresh. No data migrations or production
access are required for this preparatory change.
