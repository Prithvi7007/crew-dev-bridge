# REA tools through the existing CREW secure MCP tunnel

**No new OpenAI tunnel and no public port.** This extension adds two fixed MCP
tools to the already-connected CREW Development Bridge at 127.0.0.1:8777:

- `rea_bridge_capabilities`: report whether the separate local relay is installed
- `rea_bridge_status`: make a read-only `binary_session` REA call
- `rea_bridge_inspect_artifact(filename)`: inspect one authorized APK/ZIP/AAB/JAR/ASAR already placed in `/opt/rea-lite/artifacts/`, return **bounded, explicitly untrusted** summary Evidence

The underlying REA service stays at `127.0.0.1:18765/mcp`. A separately installed
non-root systemd service connects to the localhost REA ingress and grants only
strictly allowlisted calls across a Unix socket. The CREW MCP container has
no direct REA TCP route, Docker socket or shell access.

The relay validates Linux `SO_PEERCRED` (must be UID 10001), accepts only
`capabilities`, `status`, and `inspect_artifact`, and never accepts arbitrary
REA tools, URLs, absolute paths, or user-defined commands. It rejects traversal,
symlinks, hardlinks, nested paths, files larger than 64 MiB, and unsupported
extensions. Only operator-staged files are available to inspect; there is no
remote-upload API. It serializes all REA sessions to match REA's single-client
HTTP transport. The initial feature intentionally does **not** offer browser,
native-process, device, shell, runtime, or mutating REA tools.

## One-time installation after review and merge

**The existing bridge cannot install a new systemd service** because that
would bypass its host trust boundary. To authorize this tightly scoped host
agent, review `host/rea_relay.py`, `bridge/rea_gateway.py`,
`deploy/rea-bridge/install.sh`, the systemd unit and the Compose mount in
the merged PR. Then on the VPS:

```bash
cd /opt/crew-dev-bridge
git checkout main
git pull --ff-only origin main
sudo bash deploy/rea-bridge/install.sh
```

The installer fails closed if the REA Lite smoke test fails, if the bridge
checkout is not reviewed clean main, or if the relay was previously installed.
It installs an independent `crew-rea-relay.service` running as `nobody`, then
recreates **only** the CREW development bridge container to add the socket
mount and MCP tools. This briefly interrupts MCP calls during restart; it does
not restart `crew.service`, `wedding.service` or host Nginx.

A user-authorized archive can be placed in `/opt/rea-lite/artifacts` with
the operator's usual SFTP tool. It should be a regular single-link file of
64 MiB or less, readable by the non-root REA container. **Don't mount
production files or upload sensitive data or secrets.**

## Verification

After install, use `rea_bridge_capabilities` and `rea_bridge_status` in
ChatGPT through the **existing CREW MCP connector**. Verify a small
operator-approved archive by filename only using
`rea_bridge_inspect_artifact(filename="example.zip")`.

Output is intentionally bounded to a short excerpt of an Evidence summary.
Truncation is disclosed; it must not be interpreted as full forensic coverage.
Treat all tool-returned target data as untrusted.

## Rollback

Stop the new relay with `sudo systemctl disable --now crew-rea-relay.service`.
That does **not** affect the REA Lite Docker project or existing CREW services.
The new MCP functions return `rea_relay_unavailable` until relay is restored.

Full removal of the Compose mount requires a separate reviewed bridge change.
Never grant the bridge Docker socket or arbitrary host command execution.
