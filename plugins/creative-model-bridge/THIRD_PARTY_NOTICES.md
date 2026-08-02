# Third-party notices

Release executables are built with the locked Pixi environment recorded in
`pixi.lock`. The bridge itself uses Python's standard library.

- **PyInstaller 6.x** — GPL-2.0-or-later with the PyInstaller bootloader
  exception. The exception permits redistribution of applications bundled by
  the bootloader; see the package's `COPYING.txt` and `COPYING.APL` files in
  the build environment.
- **Python 3.13** (the locked build interpreter) — Python Software Foundation
  License. The executable embeds the interpreter under that license.
- **altgraph, macholib, pefile, pyinstaller-hooks-contrib, setuptools and
  related locked build helpers** — retain their upstream license and notice
  files from the corresponding Pixi packages. They are build/runtime support
  dependencies selected by PyInstaller; no provider credential or source
  checkout is bundled.

This notice is a routing record, not a replacement for the full license texts
shipped by the locked packages. Before publishing a tag, the release job must
archive the exact dependency license files produced by the selected Pixi
environment if the distribution policy requires them.
