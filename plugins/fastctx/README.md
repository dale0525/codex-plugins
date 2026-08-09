# FastCtx ownership

`engine/` is a direct vendor of `yc-duan/fastctx` commit
`86dac0c99efae7859ed2be468f68c16e58f5e16a`, excluding its `.git` and
`.github` directories. It is redistributed under Apache-2.0; see
`third-party/fastctx-LICENSE-APACHE` and `third-party/fastctx-NOTICE`.

The checked `runtime-release.json` pins the repository-owned
`fastctx-v0.2.5` release and its four platform archives. Do not manually edit
release hashes. To update metadata for a later explicit `fastctx-v*` release,
download its four archives and run:

```sh
pixi run python scripts/write_fastctx_runtime_release.py \
  --version <version> --tag fastctx-v<version> --assets-dir <downloaded-assets> \
  --cargo-manifest plugins/fastctx/engine/Cargo.toml \
  --output plugins/fastctx/runtime-release.json
pixi run python scripts/write_fastctx_runtime_release.py \
  --check plugins/fastctx/runtime-release.json \
  --cargo-manifest plugins/fastctx/engine/Cargo.toml
```

Review the generated metadata and `SHA256SUMS` against the release page, commit
that cutover, then install/repair with the platform provisioner and run its
`status` action. To roll back an unverified cutover, restore the prior committed
`runtime-release.json`; never substitute guessed asset hashes.
