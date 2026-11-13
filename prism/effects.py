# SPDX-License-Identifier: GPL-3.0-only
"""Original spatial effect renderer; independent of hardware and GUI."""
import colorsys
import math

EFFECTS = ('Solid', 'Rainbow wave', 'Gradient', 'Breathing', 'Color cycle',
           'Scanner', 'Comet', 'Twinkle', 'Fire', 'Plasma', 'Ripple',
           'Checkerboard', 'Audio pulse', 'Audio spectrum', 'Screen ambience',
           'CPU meter', 'Game event')

def color(value):
    if not isinstance(value, str) or len(value) != 7 or value[0] != '#':
        raise ValueError('Colours must use #RRGGBB.')
    return tuple(int(value[i:i+2], 16) for i in (1, 3, 5))

def mix(a, b, t):
    t = min(1, max(0, t))
    return tuple(round(x + (y-x)*t) for x, y in zip(a, b))

def hsv(h, s=1, v=1):
    return tuple(round(c*255) for c in colorsys.hsv_to_rgb(h % 1, s, min(1, max(0, v))))

def points(count, layout):
    """Place an LED strip or grid in normalized desk space, with rotation."""
    columns = max(1, int(layout.get('columns', count or 1)))
    rows = max(1, math.ceil(count/columns))
    angle = math.radians(layout.get('rotation', 0))
    x, y = layout.get('x', .1), layout.get('y', .25)
    w, h = layout.get('width', .8), layout.get('height', .15)
    result = []
    for i in range(count):
        px = (i % columns)/max(1, columns-1) - .5
        py = (i // columns)/max(1, rows-1) - .5 if rows > 1 else 0
        px, py = px*w, py*h
        result.append((x+w/2 + px*math.cos(angle)-py*math.sin(angle),
                       y+h/2 + px*math.sin(angle)+py*math.cos(angle)))
    return result

def render(effect, coords, elapsed, settings, inputs=None):
    if effect not in EFFECTS:
        raise ValueError('Unknown effect.')
    inputs = inputs or {}
    a, b = color(settings.get('color', '#B6A7FF')), color(settings.get('secondary', '#45E0C2'))
    brightness = settings.get('brightness', 80)/100
    t = elapsed * settings.get('speed', 50)/50
    angle = math.radians(settings.get('angle', 0))
    output = []
    for i, (x, y) in enumerate(coords):
        u = x*math.cos(angle) + y*math.sin(angle)
        phase = u - t*.15
        if effect == 'Solid': c = a
        elif effect == 'Rainbow wave': c = hsv(phase)
        elif effect == 'Gradient': c = mix(a, b, (math.sin(phase*math.tau)+1)/2)
        elif effect == 'Breathing': c = mix((0,0,0), a, (.5+.5*math.sin(t*2))**2)
        elif effect == 'Color cycle': c = hsv(t*.08)
        elif effect == 'Scanner': c = mix((0,0,0), a, max(0, 1-abs(u-(math.sin(t*2)+1)/2)*7))
        elif effect == 'Comet': c = mix((0,0,0), a, max(0, 1-((t*.2-u)%1)*9))
        elif effect == 'Twinkle': c = mix(a, b, (math.sin(i*12.9898 + math.floor(t*3)*78.233)*43758.5453)%1) if (math.sin(i*31.7+t*2)+1)/2 > .85 else (0,0,0)
        elif effect == 'Fire': c = hsv(.01+.12*(math.sin(u*13+t*4)+1)/2, 1, .15+.85*(math.sin(u*21+t*6)+1)/2)
        elif effect == 'Plasma': c = hsv((math.sin(x*8+t)+math.cos(y*9+t*.7)+math.sin((x+y)*6-t))*.16+t*.05)
        elif effect == 'Ripple': c = mix(a,b,(math.sin(math.hypot(x-.5,y-.5)*20-t*4)+1)/2)
        elif effect == 'Checkerboard': c = a if (math.floor(x*8+t)+math.floor(y*6))%2 else b
        elif effect == 'Audio pulse': c = mix((0,0,0), a, min(1,inputs.get('audio',0)*settings.get('gain',4)))
        elif effect == 'Audio spectrum':
            bands = inputs.get('bands', [0]*8)
            band = min(len(bands)-1,max(0,int(x*len(bands))))
            c = hsv(x*.75,1,min(1,bands[band]*settings.get('gain',4)))
        elif effect == 'Screen ambience':
            image = inputs.get('screen')
            c = image[min(len(image)-1,max(0,int(y*len(image))))][min(len(image[0])-1,max(0,int(x*len(image[0]))))] if image else (0,0,0)
        elif effect == 'CPU meter': c = mix(a,b,inputs.get('cpu',0)/100) if x <= inputs.get('cpu',0)/100 else (0,0,0)
        else:
            age = max(0, elapsed-inputs.get('event_time',-100))
            c = mix((0,0,0),color(inputs.get('event_color','#FF6F91')),max(0,1-age/2))
        output.append(tuple(round(min(255,max(0,v*brightness))) for v in c))
    return output
