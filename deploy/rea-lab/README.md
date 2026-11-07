# REA Lab — isolated static investigation worker

This adds an **opt-in** investigation workspace alongside the existing REA Lite
and CREW tunnel, without changing CREW Games, wedding services, Nginx or
existing REA evidence. It does **not** run third-party APKs.

## Implemented capabilities

- Verified SHA-256 input identity from existing REA managed vault.
- ZIP/XAPK inventory with archive path/traversal and expanded-size validation.
- Android APK/XAPK/AAB/JAR decompilation with JADX 1.5.6 (verified official
  release SHA-256), retaining searchable approximate Java/resources.
- Native ELF library inventory and headers via `readelf`; reports it is **not**
  native function decompilation.
- Persistent per-job status, source evidence, provenance, reports, line-based
  search (up to 50 hits per query), job listing, and bounded source-line retrieval.
  Results are not deleted automatically.
- Strict filename/job-ID allowlisting, one job at a time to avoid contention,
  private bearer auth, no dynamic app execution, no Internet route from worker.

Supported modes: `inventory`, `android`, `native`.
The current native mode inventories libraries and ELF headers only. Ghidra,
interactive function tracing, ADB/device execution, live network capture,
and autonomous feature agents **are not yet implemented**; don't mistake this
for complete reverse engineering or live app behavior.

## Why this is isolated

The current VPS has approximately 4 GiB RAM and shares resources with CREW
Games and the wedding app. The worker has a 2.3 GiB memory limit, a 1.5 CPU
limit, an offline Docker internal network, and reads the existing vault
read-only. Each investigation is on-demand; the API stays mostly idle.
Large APKs (including Ring) can still fail JVM OOM; failure is reported
rather than silently running with unlimited memory or modifying other apps.
JADX's per-job Java home and XDG config/cache/data directories live under the
writable /work volume, not /nonexistent or the read-only container image.

Disk budget: require 4 GiB free before each analysis; cap vault artifacts at
512 MiB and archive expanded-size at 1.5 GiB. These operational constraints
are explicit, not feature or user policy rules. Work/results Docker volumes
remain until explicitly removed; do **not** run `docker compose down -v`
unless intentionally discarding evidence. The workspace may grow over time.
The user/operator is responsible for approved app analysis and lawful access.

## Reviewed deployment

One-time operator action after review, PR merge, and CI checks:

```bash
cd /opt/crew-dev-bridge
git checkout main
git pull --ff-only origin main
sudo bash deploy/rea-lab/install.sh
```

Installer verifies clean main, existing REA relay/vault, service boundaries,
loopback availability and disk availability; refuses to replace an existing
REA Lab installation; generates a 256-bit local auth token. It starts only
`rea-lab` Compose, then updates only the REA relay and CREW MCP bridge
components (brief connector reconnection). Existing REA Lite and game/wedding
services are not restarted. Review installer and diff before execution.

The worker is available only at `127.0.0.1:18766` (no public port or
second ChatGPT tunnel). Token is owner-readable by `nobody`, never displayed.
The existing CREW tunnel exposes named, allowlisted API operations;
the ChatGPT connector may need a refresh after upgrade.

Example workflow:
1. `rea_vault_info("Ring_3.115.0.xapk")` and verify SHA-256.
2. `rea_lab_submit(filename="Ring_3.115.0.xapk", mode="android")`.
3. Poll `rea_lab_status(job_id)` until completed.
4. `rea_lab_search(job_id, query="LiveSessionConnector")`.
5. `rea_lab_report(job_id)` to view provenance and unknowns.

**Security:** never put secrets or unauthorized data in the sample. Uploaded
samples and decompiled outputs are untrusted. No Docker socket, unbounded
remote shell, arbitrary filesystem paths or REA tool calls are delegated.
