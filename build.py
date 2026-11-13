# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Prism Light contributors
"""Build a portable folder on the target OS; retain notices and app source."""
from pathlib import Path
from importlib.metadata import distributions
import shutil
import subprocess
import sys

root=Path(__file__).resolve().parent
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir',
                '--windowed','--name','PrismLight',str(root/'main.py')],cwd=root,check=True)
bundle=root/'dist'/'PrismLight'
for name in ('LICENSE','README.md','THIRD_PARTY_NOTICES.md','requirements.txt','requirements-build.txt','build.py','main.py','Setup-Windows.cmd','Run-PrismLight.cmd','Demo-Windows.cmd','Build-Windows.cmd','Run-PrismLight.sh'):
    shutil.copy2(root/name,bundle/name)
for name in ('prism','tests','licenses','docs'):
    shutil.copytree(root/name,bundle/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
notices=bundle/'licenses'/'installed-package-notices'
for distribution in distributions():
    package=distribution.metadata['Name']
    for f in distribution.files or []:
        if 'dist-info' in str(f) and (f.name in ('METADATA','COPYING','COPYING.txt','LICENSE','LICENSE.txt','LICENSE.md') or 'licenses' in f.parts):
            source=Path(distribution.locate_file(f))
            if source.is_file():
                target=notices/package/str(f)
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,target)
(bundle/'licenses'/'Python-copyright.txt').write_text(sys.copyright,encoding='utf-8')
if sys.platform == 'win32':
    archive = shutil.make_archive(str(root/'dist'/'PrismLight-windows'), 'zip', root/'dist', 'PrismLight')
    print(f'Windows portable ZIP: {archive}')
else:
    archive = shutil.make_archive(str(root/'dist'/'PrismLight-Studio-linux'), 'zip', root/'dist', 'PrismLight')
    print(f'Linux portable ZIP: {archive}')
print(f'Portable folder: {bundle}. Keep the entire folder together.')
