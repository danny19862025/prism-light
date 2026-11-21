# SPDX-License-Identifier: GPL-3.0-only
"""Install or remove Prism Monitor's Linux frame timing layer for the current user.

The layer (monitor/vklayer/prism_fps_layer.c, built by tools/build_fps_layer.py) is
copied to ~/.local/share/prism-monitor/ and registered as an implicit Vulkan layer in
~/.local/share/vulkan/implicit_layer.d/, where the Vulkan loader (and Steam's container
runtime) finds it. It needs no administrator rights and is removed again when FPS is
turned off. DISABLE_PRISM_FPS=1 turns it off for a single game.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

LAYER_NAME = 'VK_LAYER_PRISM_monitor_fps'
LIBRARY = 'libVkLayer_prism_fps.so'


def data_home():
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')


def manifest_path():
    return data_home() / 'vulkan' / 'implicit_layer.d' / 'prism_monitor_fps.json'


def installed_library():
    return data_home() / 'prism-monitor' / LIBRARY


def built_library(root=None):
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    return root / 'vendor' / 'vklayer' / LIBRARY


def manifest(library):
    return {'file_format_version': '1.0.0',
            'layer': {'name': LAYER_NAME, 'type': 'GLOBAL', 'library_path': str(library),
                      'api_version': '1.3.0', 'implementation_version': '1',
                      'description': 'Prism Monitor frame timing (FPS)',
                      'functions': {'vkNegotiateLoaderLayerInterfaceVersion': 'vkNegotiateLoaderLayerInterfaceVersion'},
                      'disable_environment': {'DISABLE_PRISM_FPS': '1'}}}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def status(source=None):
    """'installed', 'outdated', 'missing' or 'unbuilt'."""
    source = Path(source) if source else built_library()
    if not source.is_file():
        return 'unbuilt' if not installed_library().is_file() else 'installed'
    if not (manifest_path().is_file() and installed_library().is_file()):
        return 'missing'
    return 'installed' if _digest(source) == _digest(installed_library()) else 'outdated'


def install(source=None):
    """(ok, message). Copies the built layer and registers it."""
    source = Path(source) if source else built_library()
    state = status(source)
    if state == 'installed':
        return True, ''
    if state == 'unbuilt':
        return False, 'The FPS layer is not built yet: run tools/build_fps_layer.py.'
    target = installed_library()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix('.so.new')
        shutil.copyfile(source, temp)
        os.replace(temp, target)   # a running game keeps its mapped copy
        path = manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest(target), indent=2) + '\n', encoding='utf-8')
    except OSError as e:
        return False, f'Could not install the FPS layer: {e}'
    return True, ''


def uninstall():
    try:
        manifest_path().unlink(missing_ok=True)
        installed_library().unlink(missing_ok=True)
    except OSError as e:
        return False, f'Could not remove the FPS layer: {e}'
    return True, ''
