# SPDX-License-Identifier: GPL-3.0-only
"""Remember what was running so Prism Light can resume after a restart."""
import json

DEFAULTS = {'effects_playing': False, 'scene': None, 'display_on': False, 'fans_active': False, 'fan_headers': {}}
FAN_FIELDS = {'mode': str, 'fixed': int, 'pump': bool}


def load(path):
    session = dict(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return session
    if not isinstance(data, dict):
        return session
    for key in ('effects_playing', 'display_on', 'fans_active'):
        if isinstance(data.get(key), bool):
            session[key] = data[key]
    if isinstance(data.get('scene'), dict):
        session['scene'] = data['scene']
    headers = data.get('fan_headers')
    if isinstance(headers, dict):
        session['fan_headers'] = {
            str(k): {f: v[f] for f, kind in FAN_FIELDS.items() if isinstance(v.get(f), kind)}
            for k, v in headers.items() if isinstance(v, dict)}
    return session


def save(path, session):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({k: session.get(k, v) for k, v in DEFAULTS.items()}, indent=1), encoding='utf-8')
        tmp.replace(path)
    except (OSError, TypeError, ValueError):
        pass
