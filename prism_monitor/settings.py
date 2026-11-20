# SPDX-License-Identifier: GPL-3.0-only
"""Preferences stored as JSON in %APPDATA%\\PrismMonitor\\settings.json."""
import json
import os
import sys
from pathlib import Path

from .model import BY_KEY, DEFAULT_VISIBLE, METRICS

# Metrics that existed before 'known_metrics' was stored. A default-on metric added later is
# switched on once for existing users instead of staying hidden behind an old 'visible' list.
LEGACY_METRICS = tuple(m.key for m in METRICS if m.key != 'battery')

DEFAULTS = {
    'theme': 'dark',                 # dark | midnight | light
    'accent': '#7c9cff',
    'font_size': 10,                 # pt, base text size
    'opacity': 0.82,                 # background opacity 0.2–1.0
    'scale': 1.0,                    # 0.6–2.0
    'style': 'poster',               # poster (no panel, like the Still clock) | card
    'poster_hero': 'cpu',            # big number in the Poster look when no game runs: cpu | gpu
    'orientation': 'horizontal',     # horizontal | vertical (Card look)
    'graphs': True,
    'header': True,                  # title bar with clock and keep-awake button
    'color_levels': True,            # tint hot temperatures / near-full memory amber and red
    'history': 60,                   # samples kept per graph
    'visible': list(DEFAULT_VISIBLE),
    'known_metrics': [m.key for m in METRICS],
    'battery_wake_bt': True,         # switch Bluetooth PlayStation controllers to the report that carries battery
    'fans_visible': None,            # None = fans reporting > 0 rpm when first seen
    'interval': 1.0,                 # seconds
    'always_on_top': True,
    'locked': False,
    'click_through': False,
    'launch_at_login': False,
    'helper_autostart': False,       # start the elevated sensor helper on launch (shows UAC)
    'helper_task': False,            # one-time setup done: start the protected helper task, no prompt
    'setup_hint_shown': False,
    'fps_process': '',               # '' = foreground application, else exe name
    'cpu_temp_sensor': '',           # '' = automatic (Tctl/Tdie), else a LibreHardwareMonitor sensor name
    'position': None,                # [x, y] in virtual-desktop coordinates
    'shown': True,
    # Keep awake (the per-session after-action is deliberately not stored)
    'awake_display': False,          # also keep the display on (caffeinate -d)
    'awake_minutes': 60,             # last custom duration
    'awake_net_kbps': 150,           # download counts as active above this rate
    'awake_quiet_min': 2.0,          # quiet minutes before a download / CPU job counts as finished
    'awake_cpu_pct': 20,             # CPU job counts as finished below this load
    'awake_folder': '',              # '' = the Downloads folder
    'awake_auto_apps': [],           # exe names that keep the PC awake while running
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
    out['theme'] = out['theme'] if out['theme'] in ('dark', 'midnight', 'light') else 'dark'
    accent = str(out['accent'])
    out['accent'] = accent if len(accent) == 7 and accent.startswith('#') else DEFAULTS['accent']
    out['font_size'] = _clamp(out['font_size'], 7, 20, 10)
    out['opacity'] = _clamp(out['opacity'], 0.2, 1.0, 0.82)
    out['scale'] = _clamp(out['scale'], 0.6, 2.0, 1.0)
    # Settings saved before the Poster look existed keep the Card look they had.
    out['style'] = out['style'] if 'style' in data and out['style'] in ('poster', 'card') else 'card'
    out['poster_hero'] = out['poster_hero'] if out['poster_hero'] in ('cpu', 'gpu') else 'cpu'
    out['orientation'] = out['orientation'] if out['orientation'] in ('horizontal', 'vertical') else 'horizontal'
    out['history'] = _clamp(out['history'], 10, 300, 60)
    out['interval'] = _clamp(out['interval'], 0.25, 10.0, 1.0)
    out['visible'] = [k for k in out['visible'] if k in BY_KEY] if isinstance(out['visible'], list) else list(DEFAULT_VISIBLE)
    known = data.get('known_metrics') if isinstance(data.get('known_metrics'), list) else LEGACY_METRICS
    for metric in METRICS:
        if metric.default and metric.key not in known and metric.key not in out['visible']:
            out['visible'].append(metric.key)
    out['known_metrics'] = [m.key for m in METRICS]
    if out['fans_visible'] is not None and not isinstance(out['fans_visible'], list):
        out['fans_visible'] = None
    pos = out['position']
    out['position'] = [int(pos[0]), int(pos[1])] if isinstance(pos, (list, tuple)) and len(pos) == 2 else None
    out['fps_process'] = str(out['fps_process'] or '')[:260]
    out['cpu_temp_sensor'] = str(out['cpu_temp_sensor'] or '')[:64]
    out['awake_minutes'] = _clamp(out['awake_minutes'], 1, 24 * 60, 60)
    out['awake_net_kbps'] = _clamp(out['awake_net_kbps'], 1, 100000, 150)
    out['awake_quiet_min'] = _clamp(out['awake_quiet_min'], 0.25, 60.0, 2.0)
    out['awake_cpu_pct'] = _clamp(out['awake_cpu_pct'], 1, 99, 20)
    out['awake_folder'] = str(out['awake_folder'] or '')[:1024]
    apps = out['awake_auto_apps']
    out['awake_auto_apps'] = [str(a)[:260] for a in apps if a][:50] if isinstance(apps, list) else []
    for key in ('graphs', 'header', 'color_levels', 'awake_display', 'always_on_top', 'locked', 'click_through', 'launch_at_login', 'helper_autostart', 'helper_task', 'battery_wake_bt',
                'setup_hint_shown', 'shown'):
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
