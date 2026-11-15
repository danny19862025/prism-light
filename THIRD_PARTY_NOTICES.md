# Third-party notices

Prism Light is original GPL-3.0-only software; copyright (C) 2026 Prism Light contributors. The full license is in LICENSE. No SignalRGB binaries, scripts, QML, graphics or runtime are bundled. Architecture research used a locally supplied installer. The optional reference importer reads names and USB IDs from caller-supplied recovered source as inert text; it does not execute or redistribute that source.

## OpenRGB 1.1 (bundled separate process)

Upstream: https://codeberg.org/OpenRGB/OpenRGB and https://openrgb.org/releases.html

Prism Light bundles the official x86-64 OpenRGB 1.1 runtime as a separate executable process. Its exact commit is `8e16de27858cf2be6af54a33ce003df74cb9fd3e`. Original author/contributor notices are retained. The runtime is not linked into Prism Light's Python/Qt process; the independently implemented SDK v3 client communicates on a private loopback port.

`tools/fetch_openrgb.py` pins release and source SHA-256 digests. Each portable app contains `hardware/openrgb/manifest.json`, `OpenRGB-LICENSE.txt`, `NOTICE.txt`, the matching source tarball under `source/`, and the original runtime files and their supplied library notices. The source archive is copied without modification; the runtime comes directly from the official AppImage/portable ZIP. Source-only distributions include the reproducible fetch tool instead of the binary assets.

OpenRGB's upstream license text is GPL version 2; individual files retain their applicable terms and copyright notices. The historical SDK-reference license remains under `licenses/OpenRGB-GPL-2.0.txt`. Keep the bundled runtime notices and exact source archive when redistributing. For public releases, review corresponding-source obligations for every bundled runtime library as well as OpenRGB and Qt; the local development bundle does not itself certify public-release readiness. Bundling does not install the Windows PawnIO kernel driver or grant Linux device permissions.

## Qt for Python / PySide6 / Shiboken6 / Qt

Version pinned: 6.10.1. Copyright The Qt Company Ltd. and contributors; individual Qt third-party components retain their respective notices. PySide6 metadata lists `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`. This GPLv3 application can use the available GPLv3 terms. Qt license texts (including LGPLv3 and relevant exceptions) are retained under `licenses/Qt-license-texts/`. These texts are unmodified. Package metadata is copied by build.py to `licenses/installed-package-notices/` in the portable bundle.

Official licensing overview and detailed component notices:
https://doc.qt.io/qtforpython-6/licenses.html
https://doc.qt.io/qt-6/licenses-used-in-qt.html

Corresponding source for the exact pinned versions:
https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.1-src/
https://download.qt.io/archive/qt/6.10/6.10.1/submodules/

For public binary redistribution, provide corresponding source for the bundled Qt/PySide/Shiboken and other applicable copyleft components alongside the release, with the original component copyright/attribution files. Include this app's source (build.py also copies the app source and tests into the development bundle). Upstream source URLs document provenance; they are not a substitute for the distributor's source obligations. The current portable bundle is a local development build, not a public release.

## Python

Python Software Foundation and contributors. PSF license text is retained in `licenses/Python-PSF-2.0.txt`. The build copies the executing interpreter's full copyright text and installed package metadata/notices into the bundle. The bundled Python version is determined by the build interpreter (Windows workflow uses Python 3.12).

## PyInstaller (build tool and bootloader)

Version pinned: 6.17.0. Copyright (c) 2005–2023, PyInstaller Development Team. GPL-2.0-or-later with the PyInstaller bootloader exception; full upstream notice and exception are preserved at `licenses/PyInstaller-COPYING.txt`.
https://github.com/pyinstaller/pyinstaller/tree/v6.17.0

The folder build preserves shared Qt libraries and includes the project's licenses. Dependency notices are not removed or rewritten. Before publishing a binary, review the exact built dependency inventory and include all matching source and third-party attribution files for that release.

## Intel PresentMon 2.6.0 (FPS readout)

`vendor/presentmon/PresentMon.exe`, unmodified console release from https://github.com/GameTechDev/PresentMon/releases/tag/v2.6.0 (Authenticode: Intel Corporation; SHA-256 b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af). MIT licence: `licenses/PresentMon-MIT.txt`.

## PawnIO.Modules 0.2.11 — AMDFamily17 (Ryzen temperature)

`vendor/pawnio-modules/AMDFamily17.bin`, unmodified signed module from https://github.com/namazso/PawnIO.Modules/releases/tag/0.2.11 (release ZIP SHA-256 43608cb89bc84247fef1368a139013f7d043e17db6d6c8dfc9b46bf0905a81f4). LGPL-2.1: `licenses/PawnIO-Modules-LGPL-2.1.txt`. Used read-only (SMN 0x59800).

## Reference implementations (no code copied)

Protocol and register details were taken from OpenLinkHub (GPL-3.0, Nautilus LCD), LibreHardwareMonitor (MPL-2.0, ITE fan registers, Ryzen Tctl) and AMD's ADL SDK headers (MIT, GPU sensor IDs).
