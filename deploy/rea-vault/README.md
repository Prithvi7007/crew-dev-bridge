# REA Managed Artifact Vault

Dedicated storage for authorized inspection files. Host directory:
/opt/rea-lite/managed. Container read-only mount: /work/managed.
Incomplete chunks live in /opt/rea-lite/managed/.uploads, inaccessible to REA.
No CREW, wedding, or legacy artifact directory can be listed or deleted.

## Existing tunnel tools (after installation and ChatGPT refresh)

- rea_vault_list: filenames, sizes, quota.
- rea_vault_info(filename): SHA-256 and size for exact file.
- rea_vault_upload_begin(filename, size, sha256): new resumable transfer.
- rea_vault_upload_chunk(upload_id, offset, data_b64): one 64-KiB raw chunk max.
- rea_vault_upload_status(upload_id): resume verified offset.
- rea_vault_upload_finish(upload_id): publish after full hash/length validation.
- rea_vault_upload_cancel(upload_id): discard a partial upload.
- rea_vault_inspect(filename): static REA archive inventory.
- rea_vault_delete(filename, expected_sha256): permanently delete ONLY the
  named file from this directory when the user explicitly requests deletion.

Limits: 512 MiB per file, 1.5 GiB aggregate reservation, 30 files and
3 outstanding uploads. Extensions: .apk .xapk .aab .zip .jar .asar.
No overwrite, no symlink, no hardlink, no nested paths. No URL fetch or
unrestricted remote shell. Finalized files persist until explicitly deleted.
Deleting here does NOT delete a ChatGPT Library or local computer copy.

## One-time installation after reviewed merge

Run these commands on the VPS:

    cd /opt/crew-dev-bridge
    git checkout main
    git pull --ff-only origin main
    sudo bash deploy/rea-vault/install.sh

The installer verifies deployed files against the PR #25 merge, makes local
configuration backups, creates /opt/rea-lite/managed separately, updates the
REA container's read-only mount, restarts REA and ingress, installs the bounded
host relay, then rebuilds ONLY the CREW development MCP bridge. It keeps
existing tunnel configuration and leaves CREW Games and wedding apps untouched.
No existing REA Evidence volume is deleted.

Refresh the existing CREW connector actions in ChatGPT after installation.
Test rea_vault_list then rea_bridge_status. Only after those pass should
files be staged and inspected.

## Large Ring APK / XAPK

The existing Ring XAPK is 258,897,019 bytes, within the 512 MiB per-file cap.
Its original filename includes plus signs; use Ring_3.115.0.xapk in the vault.

Important limitation: uploading this file through 64 KiB MCP chunks requires
roughly four thousand calls and is not practical through normal chat.
The ChatGPT Files/Library connector cannot directly stream attachments to the
VPS via this MCP gateway. For large archives, use SFTP/WinSCP to place a file
once in /opt/rea-lite/managed. Then listing, SHA validation, REA inspection
and deletion can all be controlled from ChatGPT without further VPS work.

When uploading via root SFTP, set permissions on the completed file:

    sudo chgrp 1000 /opt/rea-lite/managed/Ring_3.115.0.xapk
    sudo chmod 0640 /opt/rea-lite/managed/Ring_3.115.0.xapk

The directory owner is the non-root relay user nobody, group 1000, mode
2750; REA runs as UID/GID 1000 and has read-only access. The private
.uploads staging directory is owner-only mode 2700 with inherited group 1000.
Use rea_vault_info to compute and verify the Ring XAPK SHA-256 against the
previous static report:

7bb9863a1bfe4ccde066bd45c5fe62927ba11823cff73309c4cdc659b868c288

This confirms continuity with the prior third-party-distributed sample, not
publisher authenticity. REA Lite's 1 GiB RAM cap could prevent complete
inspection of a large bundle. Static inspection does not perform JADX-style
Android DEX decompilation. No dynamic process execution is enabled.

## Authorization and deletion

A deletion request must be explicit and name the target file. Read its hash
via rea_vault_info immediately before invoking rea_vault_delete with the same
filename and expected_sha256. A hash mismatch fails closed. No wildcards,
recursive delete or operation outside the managed vault is available.

No new public listener, tunnel, Docker socket, arbitrary file path, or host
command is granted to the assistant. All REA tools and returned artifact
content remain untrusted and require human review.
