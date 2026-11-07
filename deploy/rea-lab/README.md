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
Games and the wedding app. The worker has a 2.3 GiB memory limit, a 0.65 CPU
limit, an offline Docker internal network, and reads the existing vault
read-only. Each investigation is on-demand; the API stays mostly idle.
JADX's JVM heap is capped at 1792 MiB (raised from 1536 MiB following a
Ring 3.115.0 heap-space failure); the container limit is unchanged and allows
approximately 508 MiB for non-heap memory. Large APKs can still fail JVM OOM.
The report includes the heap cap and OOM indicator; a nonzero JADX exit code
does not mean successful decompilation, even when job state is "completed".
Do not raise the container cap without reviewing peak usage and other services.
For base APKs at least 128 MiB, the worker processes root classes*.dex entries
in separate, single-threaded JADX subprocesses (one JVM per DEX) under the
existing 45-minute total analysis budget. Per-DEX output is namespaced and
searchable; individual failures and OOMs remain visible in the report.
This memory-conserving strategy does not decode Android resources, so it is
not equivalent to a complete APK decompilation. No additional VPS memory,
container privileges, network access, or filesystem access is granted.
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

## Integrated REA Intelligence release (single reviewed upgrade)

This is one release covering operational progress, paginated evidence reports,
search indexing, static dependency graphs, analyst evidence packets, structural
APK/ELF inventory, and a local private dashboard. It works against existing
persisted Android investigations (such as Ring) without rerunning JADX.

**Privacy and correctness boundaries:**
- The worker remains offline, read-only against the vault, rootless and limited
  to 2300m RAM/0.65 CPU. It never executes APKs or opens a device camera.
- Existing source evidence and reports stay in `lab_evidence`. The index is
  SQLite in each job's evidence directory and has bounded full-text storage
  (96 MiB source text, up to 150,000 files). The symbol/import inventory may
  be broader than full-text; use original source search for unmatched lines.
- Progress is updated after each DEX pass, not within an active JADX subprocess.
- Analysis outcomes distinguish `clean`, `partial` and `failed`. JADX exit
  code 3 can produce thousands of files while containing class errors.
- A dependency graph depicts **static imports**, not dynamic call traces.
- A security "finding" is a heuristic signal, never proof of vulnerability.
- Manifest presence, resource table presence, native libraries and ABIs are
  inventoried. Binary AndroidManifest permission decoding, resources.arsc
  decoding, native Ghidra decompilation and runtime tracing are **not** available
  yet. Existing native mode supports safe ELF header/dynamic-table inspection.
- ChatGPT is the reasoning model: `rea_lab_investigate` returns bounded,
  file/line cited evidence for AI analysis. No LLM executes inside REA, no
  external service receives the code, and there is no autonomous deployment.

**Reviewed upgrade** after merged PR and local branch update:

```bash
cd /opt/crew-dev-bridge
git checkout main
git pull --ff-only origin main
sudo bash deploy/rea-lab/upgrade-intelligence.sh
```

The script checks the reviewed checkout, backs up existing REA-only files,
rebuilds only the REA worker, refreshes the REA relay and CREW bridge, validates
authenticated capabilities, and preserves all evidence volumes. CREW MCP
connection may briefly reconnect. It does **not** deploy other applications.

**CREW tools after upgrade:** `rea_lab_summary(job_id,offset,limit)`,
`rea_lab_index(job_id)`, `rea_lab_index_status(job_id)`,
`rea_lab_index_search(job_id,query)`, `rea_lab_graph(job_id,query)`,
`rea_lab_investigate(job_id,query,focus)`. Existing source and search APIs
continue to work. Indexing is on-demand and asynchronous; check the index
status before querying. Search fallback remains available without an index.

**Dashboard:** The REA ingress remains reachable on VPS loopback
`127.0.0.1:18766` only. From your laptop, use an authenticated SSH tunnel
such as `ssh -N -L 18766:127.0.0.1:18766 user@your-vps`, then visit
`http://localhost:18766/dashboard`. Paste the private token *into the
local browser input only* (do not send it in chat). The browser keeps this
token only in volatile tab memory; no `localStorage` or external assets.
Only the dashboard shell is unauthenticated; every evidence request requires
the token. Do not expose port 18766 on the public Internet.
