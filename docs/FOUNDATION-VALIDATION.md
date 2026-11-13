# Foundation validation — 2026-11-13

Environment: Linux x86_64, Python 3.14.9, PySide6/Qt 6.10.1, PyInstaller 6.17.0. UI checks used Qt's offscreen platform.

Passed:

- 14 unittest tests, including TCP mock SDK exchange, connection refusal/timeout/disconnect, malformed packets, old protocol rejection, truncated controller data, topology changes, unsupported capabilities, demo isolation, durable preset persistence/corruption handling and UI interactions.
- Source startup smoke: `QT_QPA_PLATFORM=offscreen .venv/bin/python main.py --smoke-test`.
- Python compile check for app, tests and build script.
- Portable Linux folder build: `.venv/bin/python build.py`.
- Packaged startup smoke: `QT_QPA_PLATFORM=offscreen dist/PrismLight/PrismLight --smoke-test` (exit 0).
- Actual Qt screenshot inspected; controls no longer overlap. Content scrolls when vertical space is constrained.

Build limitation: the Linux packaging tool warned that optional TIFF image plugin dependency `libtiff.so.5` was unavailable. The app uses no TIFF images, and packaged demo startup passed. The initial Windows GitHub Actions build passed automated tests, packaging and packaged startup; its run is linked below.

Pending on the user's Windows PC:

- Windows executable build and startup, display scaling, keyboard navigation and native colour dialog.
- Actual OpenRGB negotiation, controller inventory, RGB byte order, native Direct mode behaviour, software brightness, one/multiple device writes and unplug/rescan behaviour.
- Visible lighting persistence, preset restoration after restart and coexistence with other RGB software.

No real OpenRGB server or physical RGB hardware was used for these checks. All SDK wire tests used an independent local fake server. There is no verified hardware compatibility list.

## Windows CI evidence

The [initial Windows run](https://github.com/danny19862025/prism-light/actions/runs/43362783469) completed successfully. GitHub's Windows runner with Python 3.12 passed the 14 automated tests, built `PrismLight.exe`, passed its offscreen demo startup smoke test, and uploaded the portable build artifact. This validates the executable on the CI runner; it does not verify physical RGB devices, display scaling or interactive appearance on the user's PC.

The updated workflow additionally checks `Run-PrismLight.cmd --smoke-test`, gives packaged startup a 30-second timeout and uploads a portable Windows ZIP. See the latest run for those checks.

The first added launcher check exposed `py -3` selecting Python 3.15 on the Windows runner, outside the pinned PySide6 version's supported range. Setup now selects a supported 64-bit interpreter explicitly (3.12, 3.13 or 3.14) and rejects an incompatible existing virtual environment.
