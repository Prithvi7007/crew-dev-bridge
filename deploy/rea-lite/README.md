# REA Lite — isolated VPS service

This directory is a proposed, **not yet installed**, independent REA MCP service for the CREW host. It lives in a separate Docker Compose project named `rea-lite` and has no CREW Games, wedding, database, Docker-socket, credentials, or production-app mount.

## Deployment workflow

1. Review the PR, specifically `Dockerfile`, `compose.yaml`, the preflight script, and `install.sh`. Merge to the bridge repository only after review and CI.
2. A **one-time operator installation** is still necessary. The current CREW Development Bridge cannot install Docker services and must not be given arbitrary host shell access.
3. On the VPS, from a clean, reviewed `/opt/crew-dev-bridge` checkout on main:

   ```sh
   cd /opt/crew-dev-bridge
   git pull --ff-only origin main
   sudo bash deploy/rea-lite/scripts/install.sh
   ```

   Installer requires root, the exact expected bridge checkout, clean main, Docker Engine >=28, >=1600 MiB available RAM, >=4 GiB free disk, and free loopback port 18765. It refuses to replace an existing `/opt/rea-lite` directory.

4. The installer copies only pinned deployment files into the separate root-owned `/opt/rea-lite`, builds and starts **only** the `rea-lite` Compose project, with a small localhost-published ingress proxy, and checks the actual MCP initialize/list-tools/binary_session flow. It never calls `docker compose down` for unrelated applications.

### Post-install checks (operator)

```sh
cd /opt/rea-lite
docker compose ps
python3 scripts/smoke_test.py
docker compose logs --tail=50 rea ingress
```

To stop only REA, run `cd /opt/rea-lite && bash scripts/stop.sh`. No evidence volume is deleted. To remove the container, do **not** use `down -v` unless explicitly discarding evidence. REA is deliberately isolated; static file inspections only in this phase. No Ghidra, JADX, adb, Chrome or untrusted target execution are installed. Place user-authorized test artifacts in `/opt/rea-lite/artifacts` as the VPS operator, not in any Git checkout. The container sees them read-only under `/work/artifacts`.

## Repair an existing v1 installation whose localhost MCP port is refused

Docker Engine 29.x can silently omit a published host port for a container attached **only** to an internal Docker bridge. The original v1 `rea-lite` deployment used that combination. The corrected design keeps REA on an internal-only network and adds an unprivileged ingress proxy attached to a separate non-internal bridge. Only the ingress proxy publishes `127.0.0.1:18765`.

**If v1 was already installed**, do NOT run the original installer again, delete evidence, or restart CREW. After the fix PR is merged, run from the VPS as the operator:

```sh
cd /opt/crew-dev-bridge
git checkout main
git pull --ff-only origin main
sudo bash deploy/rea-lite/scripts/repair-ingress.sh
```

The repair checks the original v1 Compose file against the reviewed merge commit and refuses modified/unexpected files, other processes using TCP 18765, or an existing ingress. It copies the new Compose and Nginx configuration into the separate root-owned `/opt/rea-lite`, starts only the ingress service, and runs the MCP handshake. It does not recreate the original REA container or modify unrelated services. On failure, collect `docker compose ps -a` and `docker compose logs --tail=80 rea ingress` from `/opt/rea-lite` for diagnosis. This networking issue is likely but not proven until live logs and a smoke test are available.

## ChatGPT connection is a separate step

The upstream REA HTTP endpoint has **no built-in authentication or TLS**. Do not publish TCP 18765 publicly, or connect through an unauthenticated Internet proxy. After local MCP smoke checks pass, use a private, authenticated OpenAI Secure MCP Tunnel if supported by the user's account/workspace. Follow `docs/tunnel.md` for setup and credential-handling cautions; no tunnel secrets live in this repository. An installed REA service is not the same as an active ChatGPT tool connection. REA HTTP supports one client session at a time.

## Security and limits

- Docker Engine 28+ is required because older localhost port publications could be reachable from neighboring hosts.
- REA runtime container has no routable Docker network; a separate unprivileged, loopback-published ingress proxy spans the private and non-internal networks. REA is nonroot, read-only rootfs, no Linux capabilities, 1 CPU, 1 GiB RAM and 128 PID limit. These are safeguards, **not a safe execution environment for hostile code**.
- The server still contains artifact and application tools with differing effects. Tool families restrict the published catalog, not detailed per-tool authorization.
- The image build pulls the pinned Node base tag and REA 6.8.0 package from upstream registries. Reproducibility and provenance should be strengthened with image digest and dependency lock before considering higher-trust usage.
- The ingress image is version-tagged but not digest-pinned; production-hardening should pin its reviewed digest.
- Keeping this separate avoids changing `compose.yaml` or restarting existing CREW and wedding applications.

Upstream: https://github.com/morluto/rea (MIT). Remote MCP reference: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
