# SPDX-License-Identifier: GPL-3.0-only
"""Preferences stored as JSON in %APPDATA%\\PrismMonitor\\settings.json."""
import json
import os
import sys
from pathlib import Path

from .model import BY_KEY, DEFAULT_VISIBLE

DEFAULTS = {
    'theme': 'dark',                 # dark | light
    'accent': '#7c9cff',
    'font_size': 10,                 # pt, base text size
    'opacity': 0.82,                 # background opacity 0.2–1.0
    'scale': 1.0,                    # 0.6–2.0
    'orientation': 'horizontal',     # horizontal | vertical
    'graphs': True,
    'history': 60,                   # samples kept per graph
    'visible': list(DEFAULT_VISIBLE),
    'fans_visible': None,            # None = fans reporting > 0 rpm when first seen
    'interval': 1.0,                 # seconds
    'always_on_top': True,
    'locked': False,
    'click_through': False,
    'launch_at_login': False,
    'helper_autostart': False,       # start the elevated sensor helper on launch (shows UAC)
    'fps_process': '',               # '' = foreground application, else exe name
    'position': None,                # [x, y] in virtual-desktop coordinates
    'shown': True,
}


def config_dir():
    if sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming')
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return base / 'PrismMonitor'


def _clamp(value, low, high, default):
    try:
        return min(high, max(low, type(default)(value)))
    except (TypeError, ValueError):
        return default


def validate(data):
    out = dict(DEFAULTS)
    if not isinstance(data, dict):
        return out
    for key, default in DEFAULTS.items():
        if key in data:
            out[key] = data[key]
    out['theme'] = out['theme'] if out['theme'] in ('dark', 'light') else 'dark'
    accent = str(out['accent'])
    out['accent'] = accent if len(accent) == 7 and accent.startswith('#') else DEFAULTS['accent']
    out['font_size'] = _clamp(out['font_size'], 7, 20, 10)
    out['opacity'] = _clamp(out['opacity'], 0.2, 1.0, 0.82)
    out['scale'] = _clamp(out['scale'], 0.6, 2.0, 1.0)
    out['orientation'] = out['orientation'] if out['orientation'] in ('horizontal', 'vertical') else 'horizontal'
    out['history'] = _clamp(out['history'], 10, 300, 60)
    out['interval'] = _clamp(out['interval'], 0.25, 10.0, 1.0)
    out['visible'] = [k for k in out['visible'] if k in BY_KEY] if isinstance(out['visible'], list) else list(DEFAULT_VISIBLE)
    if out['fans_visible'] is not None and not isinstance(out['fans_visible'], list):
        out['fans_visible'] = None
    pos = out['position']
    out['position'] = [int(pos[0]), int(pos[1])] if isinstance(pos, (list, tuple)) and len(pos) == 2 else None
    out['fps_process'] = str(out['fps_process'] or '')[:260]
    for key in ('graphs', 'always_on_top', 'locked', 'click_through', 'launch_at_login', 'helper_autostart', 'shown'):
        out[key] = bool(out[key])
    return out


class Settings:
    def __init__(self, path=None):
        self.path = Path(path) if path else config_dir() / 'settings.json'
        self.error = None
        try:
            self.data = validate(json.loads(self.path.read_text(encoding='utf-8')))
        except FileNotFoundError:
            self.data = dict(DEFAULTS)
        except (OSError, ValueError) as e:
            self.error = f'Settings were unreadable and defaults are in use ({e}).'
            try:
                self.path.replace(self.path.with_suffix('.corrupt.json'))
            except OSError:
                pass
            self.data = dict(DEFAULTS)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        os.replace(tmp, self.path)
