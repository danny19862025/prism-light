# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Prism Light contributors
import json
import os
from pathlib import Path
import tempfile
from .backend import rgb_bytes

def default_path():
    base = Path(os.environ.get('APPDATA') or os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
    return base/'PrismLight'/'presets.json'

class Presets:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
    def read(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data['version'] != 1 or not isinstance(data['presets'], dict):
                raise ValueError('Unknown preset format.')
            for name, p in data['presets'].items():
                if not isinstance(name, str) or not name.strip() or len(name)>60:
                    raise ValueError('Invalid preset name.')
                rgb_bytes(p['color'], p['brightness'])
                if p['scope'] not in ('demo', 'hardware') or not isinstance(p['devices'], list) or not all(isinstance(k,str) for k in p['devices']):
                    raise ValueError('Invalid preset devices.')
            return data['presets']
        except (ValueError, KeyError, TypeError) as e:
            raise ValueError(f'Cannot read presets at {self.path}. File preserved; restore a backup or rename it. {e}') from e
    def save(self, name, color, brightness, devices, demo):
        name = name.strip()
        if not name or len(name)>60:
            raise ValueError('Give the preset a name of 1–60 characters.')
        rgb_bytes(color, brightness)
        data = self.read()
        data[name] = dict(color=color, brightness=brightness, devices=list(devices), scope='demo' if demo else 'hardware')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as f:
                temp = f.name
                json.dump(dict(version=1, presets=data), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
