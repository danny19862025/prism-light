# SPDX-License-Identifier: GPL-3.0-only
"""Publish the Poster's content for the desktop's own widgets (KDE Plasma, GNOME Shell).

The monitor keeps collecting (sensors, FPS layer, batteries) and writes what the Poster
shows, already formatted and coloured, to one small JSON file each update:

    $XDG_RUNTIME_DIR/prism-monitor/widget.json

`widgets/plasma` and `widgets/gnome` only draw that file, so they look and read the same as
the Poster. The file is private to the user (the runtime directory is 0700), replaced
atomically, and removed when the monitor quits; `time` lets a widget tell a stale file
(the monitor was killed) from a live one.
"""
import json
import os
import time
from pathlib import Path

VERSION = 1


def path():
    base = os.environ.get('XDG_RUNTIME_DIR')
    if not base or not os.path.isdir(base):
        base = os.path.join(os.path.expanduser('~'), '.cache')
    return Path(base) / 'prism-monitor' / 'widget.json'


def _hex(color):
    return None if color is None else color.name()


def document(content, ink, interval, state=None):
    """The JSON-ready dict for one P.Content and its P.Ink."""
    return {
        'version': VERSION,
        'time': time.time(),
        'interval': interval,
        'number': content.number, 'unit': content.unit, 'headline': content.headline,
        'subline': content.subline, 'caption': content.caption,
        'text': _hex(ink.text), 'accent': _hex(ink.accent),
        'groups': [{'caption': g.caption,
                    'rows': [{'label': r.label, 'value': r.value, 'color': _hex(r.color), 'level': r.level}
                             for r in g.rows]}
                   for g in content.groups],
        'state': state or {},       # menu checkmarks: sections, hero, launch_at_login
    }


class Publisher:
    def __init__(self, target=None):
        self.target = Path(target) if target else path()
        self.failed = False

    def write(self, doc):
        try:
            self.target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = self.target.with_suffix('.tmp')
            tmp.write_text(json.dumps(doc, separators=(',', ':')), encoding='utf-8')
            os.replace(tmp, self.target)
            self.failed = False
        except OSError:
            self.failed = True   # a widget shows "not running"; the monitor itself is unaffected

    def clear(self):
        try:
            self.target.unlink()
        except OSError:
            pass
