# SPDX-License-Identifier: GPL-3.0-only
"""Build Prism Light info blocks as iCUE widgets (.icuewidget) for pump LCDs.

Output: dist/icue-widgets/<Name>.icuewidget (double-click to import into iCUE)
and dist/icue-widgets/folders/<id>/ (the same widget as a plain folder).

The package format follows Corsair-Labs/icuewidget-cli: an uncompressed ZIP of
the widget folder's contents. The head rules it checks are enforced here too:
uppercase <!DOCTYPE html>, no BOM, ASCII-only up to </head>, and one <title>
that names the widget as tr('Name') with a translation entry.
"""
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'icue-widgets' / 'src' / 'prism-blocks.js'
OUT = ROOT / 'dist' / 'icue-widgets'
VERSION = '1.0.1'

CPU_TEMP = ('cpuTempSensor', 'CPU Temperature', "plugins.Sensorsdataprovider.getDefaultSensorIdBlock('temperature', 'package')")
GPU_TEMP = ('gpuTempSensor', 'GPU Temperature', "plugins.Sensorsdataprovider.getDefaultSensorIdBlock('temperature', 'gpu-temp')")
CPU_LOAD = ('cpuLoadSensor', 'CPU Load', "plugins.Sensorsdataprovider.getDefaultSensorIdBlock('load', 'default')")
GPU_LOAD = ('gpuLoadSensor', 'GPU Load', "plugins.Sensorsdataprovider.getDefaultSensorIdBlock('load', 'gpu-load')")

# (block, widget name, description, sensors, uses FPS, uses text, colours)
WIDGETS = [
    ('gameHud', 'Prism Game HUD', 'Live FPS with CPU and GPU temperature.', [CPU_TEMP, GPU_TEMP], True, False, ('#A89BFF', '#45E0C2')),
    ('dualGauge', 'Prism Dual Gauge', 'CPU temperature arc outside, GPU temperature arc inside.', [CPU_TEMP, GPU_TEMP], False, False, ('#A89BFF', '#45E0C2')),
    ('singleGauge', 'Prism Single Gauge', 'One wide gradient arc with a big CPU temperature.', [CPU_TEMP], False, False, ('#8F9BFF', '#45E0C2')),
    ('sensorBars', 'Prism Sensor Bars', 'CPU and GPU load and temperature as bars.', [CPU_LOAD, GPU_LOAD, CPU_TEMP, GPU_TEMP], False, False, ('#A89BFF', '#45E0C2')),
    ('ringReadout', 'Prism Ring Readout', 'Spinning rings around a large CPU temperature.', [CPU_TEMP], False, False, ('#A89BFF', '#45E0C2')),
    ('hexPanels', 'Prism Hex Panels', 'Four glowing hexagon tiles over a drifting star field.', [CPU_LOAD, GPU_LOAD, CPU_TEMP, GPU_TEMP], False, True, ('#3DF08A', '#D2FFE0')),
    ('sweepGauge', 'Prism Sweep Gauge', 'Gradient arc: GPU temperature, CPU temperature and FPS.', [GPU_TEMP, CPU_TEMP], True, True, ('#E84BD0', '#6A5BFF')),
    ('dotRingClock', 'Prism Dot Ring Clock', 'Time and CPU load inside a ring of dots with a travelling comet.', [CPU_LOAD], False, False, ('#3D8BFF', '#FFD54A')),
    ('boldSplit', 'Prism Bold Split', 'Solid colour face with CPU and GPU temperature.', [CPU_TEMP, GPU_TEMP], False, False, ('#D8262E', '#FFFFFF')),
]


def meta(content, label, kind, default, extra=''):
    return (f'\t\t<meta name="x-icue-property" content="{content}" data-label="tr(\'{label}\')" '
            f'data-type="{kind}" data-default="{default}"{extra}>\n')


def head(name, sensors, uses_text, colours):
    props = [meta('accentColor', 'Colour 1', 'color', f"'{colours[0]}'"),
             meta('secondColor', 'Colour 2', 'color', f"'{colours[1]}'"),
             meta('backgroundColor', 'Background', 'color', "'#000000'"),
             meta('speed', 'Animation Speed', 'slider', '50', ' data-min="0" data-max="200" data-step="5" data-unit-label="\'%\'"')]
    if uses_text:
        props.append(meta('label', 'Text', 'textfield', "'PRISM'"))
    for content, label, default in sensors:
        props.append(meta(content, label, 'sensors-combobox', default))
    return ('<!DOCTYPE html>\n<html lang="en">\n\t<head>\n\t\t<meta charset="UTF-8">\n'
            '\t\t<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
            f"\t\t<title>tr('{name}')</title>\n"
            '\t\t<link rel="icon" type="image/svg+xml" href="resources/icon.svg">\n'
            + ''.join(props) +
            '\t\t<style>html,body{margin:0;height:100%;background:transparent;overflow:hidden}'
            'body{display:flex;align-items:center;justify-content:center}canvas{display:block}</style>\n'
            '\t</head>\n')


def icon(colours):
    a, b = colours
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient></defs>'
            '<circle cx="32" cy="32" r="29" fill="#0b0f18" stroke="url(#g)" stroke-width="4"/>'
            '<path d="M32 13 L50 47 H14 Z" fill="url(#g)"/></svg>\n')


def check_head(html, name):
    """Corsair's import rules (icuewidget-cli head-rules.js)."""
    assert html.startswith('<!DOCTYPE html>'), 'doctype'
    part = html[:html.index('</head>')]
    assert part.isascii(), f'{name}: non-ASCII before </head>'
    assert len(re.findall(r'<title\b', part)) == 1, 'exactly one <title>'


def build(widget, script):
    block, name, description, sensors, fps, uses_text, colours = widget
    wid = 'com.prismlight.' + block.lower()
    folder = OUT / 'folders' / wid
    shutil.rmtree(folder, ignore_errors=True)
    (folder / 'resources').mkdir(parents=True)
    html = head(name, sensors, uses_text, colours) + (
        '\t<body>\n\t\t<canvas id="screen" width="480" height="480"></canvas>\n'
        f'\t\t<script>\n{script}\n\t\tstartPrismWidget({json.dumps(block)});\n\t\t</script>\n\t</body>\n</html>\n')
    check_head(html, name)
    (folder / 'index.html').write_bytes(html.encode('utf-8'))
    plugins = ['widgetbuilder.sensorsdataprovider:Sensors:1.0'] + (['widgetbuilder.fpsdataprovider:Fps:1.0'] if fps else [])
    manifest = {
        'author': 'Prism Light', 'id': wid, 'name': name, 'description': description, 'version': VERSION,
        'preview_icon': 'resources/icon.svg', 'min_app_version': '5.52', 'min_framework_version': '1.3.0',
        'os': [{'platform': 'windows'}], 'supported_devices': [{'type': 'pump_lcd'}],
        'interactive': False, 'required_plugins': plugins,
    }
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    labels = [name, 'Colour 1', 'Colour 2', 'Background', 'Animation Speed'] + (['Text'] if uses_text else []) + [s[1] for s in sensors]
    (folder / 'translation.json').write_text(json.dumps({'en': {'translation': {l: l for l in labels}}}, indent=2) + '\n', encoding='utf-8')
    (folder / 'resources' / 'icon.svg').write_text(icon(colours), encoding='utf-8')
    package = OUT / f'{name}.icuewidget'
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_STORED) as z:
        for path in sorted(p for p in folder.rglob('*') if p.is_file()):
            z.write(path, path.relative_to(folder).as_posix())
    return package


def main():
    script = SOURCE.read_text(encoding='utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob('*.icuewidget'):
        old.unlink()
    built = [build(w, script) for w in WIDGETS]
    for path in built:
        print(f'{path.stat().st_size:7d}  {path.relative_to(ROOT)}')
    print(f'{len(built)} iCUE widgets in {OUT.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
