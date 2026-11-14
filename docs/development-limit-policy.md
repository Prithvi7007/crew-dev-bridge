# Source development limits and security boundaries

The bridge does **not** impose an arbitrary maximum number of source files per change/commit, a blanket 250 KB source-file limit, or a 125 KB text-patch ceiling.

Preserved security invariants:
- Only fixed allowlisted repositories; feature-branch editing; no direct main commits.
- Reject secrets, unsafe paths, symlinks, Git workflow edits and binary/mode-changing patches.
- Keep optimistic SHA-256 write protection and audited operations.
- Require repository-specific, fingerprint-matched passing offline checks before commits.
- Keep the two explicit, integrity-checked CREW frontend **build output** size contracts; these are not source-code limits.
- Production release permissions remain independent.

Platform, GitHub API, network, transport, available disk, and Docker resource limits still apply. The bridge reports their failures rather than arbitrarily splitting features into smaller commits. Git object serialization continues to use UTF-8 text source; binary files are not supported by this interface.
