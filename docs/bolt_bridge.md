# BOLT bridge

Selector `bolt-ai` maps to `Prithvi7007/Bolt_ai`, main, and `/bolt-source`. Reuses the Career token through a read-only secret mount; no token in source or offline worker. Development-only, feature branches and draft PRs, no production access.

Offline gates: `bolt_api_tests` (FastAPI pytest) and `bolt_desktop_syntax` (Electron Node syntax only; NOT a React/TS build). Runner dependencies installed at trusted image build.

After reviewed PR merge: back up runtime config; verify idle jobs; update bridge main; run `scripts/bootstrap.sh` in a maintenance window; verify BOLT and existing selectors. Do not deploy CREW Games. Text-only 30 files/commit, no secrets.
