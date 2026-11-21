# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Prism Light contributors
"""Build the Prism Monitor widget as a portable Windows folder and ZIP.

    .venv\\Scripts\\python build_monitor.py

Output: dist/PrismMonitor/PrismMonitor.exe and dist/PrismMonitor-windows.zip.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
if sys.platform != 'win32':
    raise SystemExit('Prism Monitor is a Windows widget; build it on Windows.')
subprocess.run([sys.executable, str(root / 'tools' / 'fetch_sensors.py')], cwd=root, check=True)
subprocess.run([sys.executable, str(root / 'tools' / 'fetch_monitor_deps.py')], cwd=root, check=True)

# Application icon, drawn by the same code as the tray icon.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QGuiApplication  # noqa: E402
app = QGuiApplication([])
sys.path.insert(0, str(root))
from prism_monitor.app import make_icon  # noqa: E402
icon = root / 'build' / 'PrismMonitor.ico'
icon.parent.mkdir(exist_ok=True)
make_icon('#7c9cff').pixmap(64, 64).save(str(icon))

excluded = ['PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtWebEngineCore',
            'PySide6.QtWebEngineWidgets', 'PySide6.QtMultimedia', 'PySide6.QtPdf', 'PySide6.Qt3DCore',
            'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtOpenGL', 'PySide6.QtSql',
            'PySide6.QtTest', 'PySide6.QtBluetooth', 'PySide6.QtPositioning', 'hid', 'prism', 'tkinter']
argv = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
        '--name', 'PrismMonitor', '--icon', str(icon), '--distpath', str(root / 'dist'),
        '--workpath', str(root / 'build' / 'monitor'), '--specpath', str(root / 'build')]
for module in excluded:
    argv += ['--exclude-module', module]
# Barlow, for the Poster look (prism_monitor/poster.py loads it from beside the module).
argv += ['--add-data', f"{root / 'prism_monitor' / 'fonts'}{os.pathsep}prism_monitor/fonts"]
subprocess.run(argv + [str(root / 'monitor_main.py')], cwd=root, check=True)

bundle = root / 'dist' / 'PrismMonitor'
# Qt pulls in Quick/QML/PDF and a software OpenGL renderer through plugins; the widget
# uses only QtCore/QtGui/QtWidgets/QtNetwork with raster painting.
qt = bundle / '_internal' / 'PySide6'
for pattern in ('opengl32sw.dll', 'Qt6Quick*.dll', 'Qt6Qml*.dll', 'Qt6Pdf*.dll', 'Qt6VirtualKeyboard*.dll',
                'plugins/imageformats/qpdf.dll', 'plugins/platforminputcontexts/*', 'plugins/platforms/qdirect2d.dll',
                'plugins/platforms/qoffscreen.dll', 'plugins/platforms/qminimal.dll', 'translations/*',
                'plugins/generic/*', 'plugins/networkinformation/*', 'plugins/tls/qopensslbackend.dll'):
    for path in qt.glob(pattern):
        if path.is_file():
            path.unlink()
for path in (bundle / '_internal').glob('libcrypto-*.dll'):
    path.unlink()
for path in (bundle / '_internal').glob('libssl-*.dll'):
    path.unlink()
shutil.copytree(root / 'vendor' / 'sensorhost', bundle / 'vendor' / 'sensorhost', dirs_exist_ok=True)
shutil.copytree(root / 'vendor' / 'presentmon', bundle / 'vendor' / 'presentmon', dirs_exist_ok=True)
licenses = bundle / 'licenses'
licenses.mkdir(exist_ok=True)
shutil.copy2(root / 'LICENSE', bundle / 'LICENSE.txt')
shutil.copy2(root / 'licenses' / 'PresentMon-MIT.txt', licenses / 'PresentMon-MIT.txt')
shutil.copy2(root / 'vendor' / 'sensorhost' / 'LibreHardwareMonitor-LICENSE.txt', licenses / 'LibreHardwareMonitor-MPL-2.0.txt')
shutil.copytree(root / 'licenses' / 'Qt-license-texts', licenses / 'Qt-license-texts', dirs_exist_ok=True)
shutil.copy2(root / 'licenses' / 'PyInstaller-COPYING.txt', licenses / 'PyInstaller-COPYING.txt')
shutil.copy2(root / 'licenses' / 'Python-PSF-2.0.txt', licenses / 'Python-PSF-2.0.txt')
shutil.copy2(root / 'docs' / 'MONITOR.md', bundle / 'README.md')
shutil.copy2(root / 'THIRD_PARTY_NOTICES.md', bundle / 'THIRD_PARTY_NOTICES.md')
shutil.copytree(root / 'monitor', bundle / 'source' / 'monitor', dirs_exist_ok=True)
shutil.copytree(root / 'prism_monitor', bundle / 'source' / 'prism_monitor', dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('__pycache__'))
archive = shutil.make_archive(str(root / 'dist' / 'PrismMonitor-windows'), 'zip', root / 'dist', 'PrismMonitor')
print(f'Portable folder: {bundle}\nZIP: {archive}')

# Windows installer (Inno Setup 6). Skipped with a note when ISCC is not installed.
from prism_monitor import __version__  # noqa: E402
candidates = [Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Inno Setup 6' / 'ISCC.exe',
              Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Inno Setup 6' / 'ISCC.exe',
              Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Inno Setup 6' / 'ISCC.exe']
iscc = shutil.which('iscc') or next((str(p) for p in candidates if p.is_file()), None)
if iscc:
    subprocess.run([iscc, '/Q', f'/DAppVersion={__version__}', str(root / 'installer' / 'PrismMonitor.iss')],
                   cwd=root, check=True)
    print(f"Installer: {root / 'dist' / f'PrismMonitor-Setup-{__version__}.exe'}")
else:
    print('Inno Setup 6 not found; installer skipped (winget install JRSoftware.InnoSetup).')
