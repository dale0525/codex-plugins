# FastCtx ownership

`engine/` is a direct vendor of `yc-duan/fastctx` commit
`86dac0c99efae7859ed2be468f68c16e58f5e16a`, excluding its `.git` and
`.github` directories. It is redistributed under Apache-2.0; see
`third-party/fastctx-LICENSE-APACHE` and `third-party/fastctx-NOTICE`.

The checked `runtime-release.json` is deliberately transitional: its locked
0.2.4 upstream assets remain digest-pinned until the owned `fastctx-v0.2.5`
release supplies all four platform archives. Do not manually edit release
hashes. After the release completes, download its four assets and run:

```sh
pixi run python scripts/write_fastctx_runtime_release.py \
  --version 0.2.5 --tag fastctx-v0.2.5 --assets-dir <downloaded-assets> \
  --output plugins/fastctx/runtime-release.json
pixi run python scripts/write_fastctx_runtime_release.py \
  --check plugins/fastctx/runtime-release.json
```

Review the generated metadata against the release page, commit that cutover,
then install/repair with the platform provisioner and run its `status` action.
To roll back an unverified cutover, restore the prior committed
`runtime-release.json`; never substitute guessed asset hashes.
