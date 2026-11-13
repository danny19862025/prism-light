# SPDX-License-Identifier: GPL-3.0-only
"""Validated, atomic scene/automation storage. Never stores executable scripts."""
import json
import math
import os
from pathlib import Path
import tempfile
from .effects import EFFECTS, color
from .presets import default_path

def validate(scene):
    if not isinstance(scene, dict) or scene.get('effect') not in EFFECTS:
        raise ValueError('Unknown scene effect.')
    color(scene['color']); color(scene['secondary'])
    for name, lo, hi in [('brightness',0,100),('speed',1,200),('angle',0,360),('gain',1,20)]:
        v = scene[name]
        if type(v) is not int or not lo <= v <= hi:
            raise ValueError('Invalid scene '+name)
    if scene.get('scope') not in ('demo','hardware') or not isinstance(scene.get('devices'),list):
        raise ValueError('Invalid scene scope/devices.')
    if not all(isinstance(k,str) and len(k)<=128 for k in scene['devices']):
        raise ValueError('Invalid device identities.')
    if not isinstance(scene.get('layouts',{}),dict) or not isinstance(scene.get('paint',{}),dict):
        raise ValueError('Invalid layouts/paint.')
    for layout in scene.get('layouts',{}).values():
        if not isinstance(layout,dict): raise ValueError('Invalid layout.')
        for key,lo,hi in [('x',-2,2),('y',-2,2),('width',.01,3),('height',.01,3),('rotation',0,360),('columns',1,65535)]:
            v = layout[key]
            if type(v) not in (int,float) or not math.isfinite(v) or not lo<=v<=hi:
                raise ValueError('Invalid layout '+key)
        if type(layout['columns']) is not int: raise ValueError('Columns must be an integer.')
    for pixels in scene.get('paint',{}).values():
        if not isinstance(pixels,dict) or len(pixels)>65535: raise ValueError('Invalid LED paint.')
        for key,value in pixels.items():
            if not key.isdigit() or not 0<=int(key)<65535: raise ValueError('Invalid LED index.')
            color(value)
    return scene

class SceneStore:
    def __init__(self,path=None):
        self.path = Path(path) if path else default_path().with_name('studio.json')
    def read(self):
        if not self.path.exists(): return {'version':1,'scenes':{},'rules':[]}
        try:
            if self.path.stat().st_size>8_000_000: raise ValueError('Scene file too large.')
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if data['version']!=1 or not isinstance(data['scenes'],dict) or not isinstance(data['rules'],list):
                raise ValueError('Unknown studio format.')
            for name,scene in data['scenes'].items():
                if not isinstance(name,str) or not 1<=len(name)<=60: raise ValueError('Invalid name.')
                validate(scene)
            if len(data['rules'])>100: raise ValueError('Too many rules.')
            for rule in data['rules']:
                if rule['kind'] not in ('Time','Application') or not isinstance(rule['value'],str) or not isinstance(rule['scene'],str):
                    raise ValueError('Invalid automation rule.')
            return data
        except (ValueError,KeyError,TypeError,AttributeError) as e:
            raise ValueError(f'Cannot read {self.path}; file preserved. {e}') from e
    def write(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=None
        try:
            with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=self.path.parent,delete=False) as f:
                temp=f.name;json.dump(data,f,indent=2);f.flush();os.fsync(f.fileno())
            os.replace(temp,self.path)
        finally:
            if temp and os.path.exists(temp):os.unlink(temp)
    def save(self,name,scene):
        name=name.strip()
        if not 1<=len(name)<=60:raise ValueError('Name must contain 1–60 characters.')
        validate(scene);data=self.read();data['scenes'][name]=scene;self.write(data)

