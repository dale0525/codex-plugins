# Git for Windows runtime notice

On Windows, the FastCtx plugin may download a pinned Git for Windows runtime
archive when no usable standalone GNU Bash is already available. The bzip2 tar
archive is downloaded directly from the official Git for Windows GitHub release
and is not redistributed in this repository.

- Project: Git for Windows
- Source: https://github.com/git-for-windows/git
- License information: https://github.com/git-for-windows/git/blob/main/COPYING
- Runtime lock: `../../windows-bash-runtime.json`

The provisioner verifies the archive size and SHA-256 digest recorded in the
runtime lock before extraction.
