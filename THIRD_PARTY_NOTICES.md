# Third-party notices

Prism Light is original GPL-3.0-only software; copyright (C) 2026 Prism Light contributors. The full license is in LICENSE. No SignalRGB binaries, scripts, QML, graphics or runtime are bundled. Architecture research used a locally supplied installer. The optional reference importer reads names and USB IDs from caller-supplied recovered source as inert text; it does not execute or redistribute that source.

## OpenRGB (separate user-installed backend)

OpenRGB upstream: https://gitlab.com/CalcProgrammer1/OpenRGB

OpenRGB's LICENSE is GNU General Public License version 2, June 1991. Its source files retain their respective author copyright notices (including Adam Honse / CalcProgrammer1 and other contributors). The official checkout used for reference is unchanged at revision `4e7aa49d68aca6c959eb2a9b48a6d6fc35f744a6`. A verbatim copy of its LICENSE is retained as `licenses/OpenRGB-GPL-2.0.txt`. No OpenRGB source, drivers, binaries, graphics or branding are incorporated into this app. The SDK documentation was used to independently implement packet encoding and parsing. OpenRGB is not included in the packaged app; users obtain it separately with its own notices.

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
