# SPDX-License-Identifier: GPL-3.0-only
"""Original spatial effect renderer; independent of hardware and GUI."""
import colorsys
import math

EFFECTS = ('Solid', 'Rainbow wave', 'Gradient', 'Breathing', 'Color cycle',
           'Scanner', 'Comet', 'Twinkle', 'Fire', 'Plasma', 'Ripple',
           'Checkerboard', 'Audio pulse', 'Audio spectrum', 'Screen ambience',
           'CPU meter', 'Game event', 'Digital rain', 'Rain', 'Snowfall',
           'Fireflies', 'Ocean waves', 'Lava lamp', 'Watercolor', 'Voronoi cells',
           'Neon tunnel', 'Galaxy', 'Meteor shower', 'Fireworks', 'Lightning storm',
           'Candy stripes', 'Sunset', 'Fire and ice', 'Radar', 'Kaleidoscope',
           'Audio bars', 'Audio rings')

NEW_EFFECTS = EFFECTS[17:]
DENSITY_EFFECTS = ('Digital rain','Rain','Snowfall','Fireflies','Voronoi cells','Meteor shower','Candy stripes','Kaleidoscope')

def noise(x,y=0):
    return (math.sin(x*127.1+y*311.7)*43758.5453)%1

def smooth_noise(x,y):
    ix,iy=math.floor(x),math.floor(y);fx,fy=x-ix,y-iy
    fx=fx*fx*(3-2*fx);fy=fy*fy*(3-2*fy)
    lower=noise(ix,iy)*(1-fx)+noise(ix+1,iy)*fx
    upper=noise(ix,iy+1)*(1-fx)+noise(ix+1,iy+1)*fx
    return lower*(1-fy)+upper*fy

def dim(c,amount):
    return mix((0,0,0),c,amount)

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
    scale=settings.get('scale',100)/100
    density=settings.get('density',50)/100
    # Shared desk-space objects avoid a separate random pattern per device.
    particles=[(noise(j,3),noise(j,7),noise(j,13)) for j in range(6+int(density*18))] if effect in ('Snowfall','Fireflies','Meteor shower') else []
    sites=[(.5+.6*math.sin(j*2.39+t*.2),.5+.6*math.cos(j*3.17+t*.17)) for j in range(5+int(density*10))] if effect=='Voronoi cells' else []
    output = []
    for i, (x, y) in enumerate(coords):
        if effect in NEW_EFFECTS:
            x=(x-.5)*scale+.5;y=(y-.5)*scale+.5
        u = x*math.cos(angle) + y*math.sin(angle)
        v = -x*math.sin(angle) + y*math.cos(angle)
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
        elif effect=='Digital rain':
            column=math.floor(u*(8+24*density))
            head=(t*(.2+.25*noise(column,1))+noise(column,2))%1
            distance=(head-v)%1
            amount=max(0,1-distance/(.1+.35*density))**2
            c=mix(dim(a,amount),b,max(0,1-distance/.025))
        elif effect=='Rain':
            column=math.floor(u*(8+20*density));center=(column+.5)/(8+20*density)
            drift=(t*(.35+.2*noise(column))+noise(column,5)-v)%1
            amount=math.exp(-abs(u-center)*90)*max(0,1-drift/.18)**2
            c=dim(mix(a,b,noise(column,7)),amount)
        elif effect=='Snowfall':
            amount=0
            for px,py,speed in particles:
                sx=px+.03*math.sin(t*.5+py*9);sy=(py+t*(.035+speed*.06))%1
                amount=max(amount,math.exp(-((u-sx)**2+(v-sy)**2)/(0.0005+.001*density)))
            c=dim(mix(a,b,(math.sin(t*.1)+1)/2),amount)
        elif effect=='Fireflies':
            amount=0
            for px,py,seed in particles:
                fx=px+.08*math.sin(t*.35+seed*8);fy=py+.08*math.cos(t*.27+seed*9)
                glow=(.5+.5*math.sin(t*(.7+seed)+py*12))**3
                amount+=glow*math.exp(-((u-fx)**2+(v-fy)**2)/.004)
            c=mix(dim(b,.025),a,min(1,amount))
        elif effect=='Ocean waves':
            wave=math.sin(v*13+math.sin(u*7+t)*1.7-t*1.2)
            foam=max(0,(wave-.75)*4)
            c=mix(mix(a,b,(wave+1)/2), (220,250,255),foam*.65)
        elif effect=='Lava lamp':
            field=0
            for j in range(4):
                lx=.5+.35*math.sin(t*.23+j*2);ly=.5+.4*math.cos(t*.31+j*1.4)
                field+=.014/((u-lx)**2+(v-ly)**2+.004)
            c=mix(dim(b,.12),a,min(1,max(0,(field-.25)*2)))
        elif effect=='Watercolor':
            wash=smooth_noise(u*3+t*.1,v*3-t*.08)
            detail=smooth_noise(u*7+wash*2,v*7+t*.06)
            c=mix(mix(a,b,wash),hsv(wash+detail*.4+t*.015,.45),.45)
        elif effect=='Voronoi cells':
            distances=sorted(((u-sx)**2+(v-sy)**2,j) for j,(sx,sy) in enumerate(sites))
            edge=min(1,max(0,(distances[1][0]-distances[0][0])*30))
            c=dim(mix(a,b,noise(distances[0][1],8)),.12+.88*edge)
        elif effect=='Neon tunnel':
            radius=max(.02,math.hypot(u-.5,v-.5));theta=math.atan2(v-.5,u-.5)
            rings=(math.log(radius)*2+t*.8)%1
            grid=abs(math.sin(theta*5+t*.15))**18
            amount=max(max(0,1-rings*7),grid*.45)
            c=dim(mix(a,b,(math.sin(theta*3+t*.3)+1)/2),amount)
        elif effect=='Galaxy':
            radius=math.hypot(u-.5,v-.5);theta=math.atan2(v-.5,u-.5)
            arms=(.5+.5*math.cos(theta*3-radius*16+t*.5))**4
            glow=max(0,1-radius*1.4)
            stars=max(0,noise(math.floor(u*70),math.floor(v*40))-.985)*50
            c=mix(dim(mix(a,b,arms),glow*(.15+.85*arms)),(235,240,255),min(1,stars))
        elif effect=='Meteor shower':
            amount=0;shade=0
            for px,py,seed in particles:
                head=(py+t*(.12+seed*.12))%1
                along=(head-v)%1;across=abs(u-(px+.3*v)%1)
                trail=max(0,1-along/.2)*math.exp(-across*120)
                if trail>amount:amount=trail;shade=seed
            c=dim(mix(a,b,shade),amount)
        elif effect=='Fireworks':
            c=(0,0,0)
            for burst in range(3):
                cycle=(t*.23+burst/3)%1
                bx=.2+.6*noise(burst+math.floor(t*.23+burst/3),9)
                by=.2+.6*noise(burst+math.floor(t*.23+burst/3),11)
                r=math.hypot(u-bx,v-by);theta=math.atan2(v-by,u-bx)
                sparks=(.3+.7*abs(math.cos(theta*11+burst)))**5
                ring=math.exp(-((r-cycle*.65)/.025)**2)*(1-cycle)*sparks
                layer=dim(mix(a,b,burst/2),ring)
                c=tuple(min(255,l+r) for l,r in zip(c,layer))
        elif effect=='Lightning storm':
            cycle=(t/7)%1;flash=math.exp(-((cycle-.15)/.015)**2)+.35*math.exp(-((cycle-.21)/.02)**2)
            bolt=.5+.08*math.sin(v*27+math.floor(t/7)*3)
            c=mix(dim(a,.08),b,min(1,flash*(.3+.7*math.exp(-abs(u-bolt)*45))))
        elif effect=='Candy stripes':
            stripe=(u+v*.5-t*.12)*(4+12*density)
            c=mix(a,b,(math.sin(stripe*math.tau)+1)/2)
        elif effect=='Sunset':
            horizon=.6+.08*math.sin(t*.2)
            sky=mix(a,b,min(1,max(0,v)))
            sun=math.exp(-((u-.5)**2+(v-horizon)**2)/.015)
            c=mix(sky,(255,225,135),sun*.85)
        elif effect=='Fire and ice':
            boundary=.5+.15*math.sin(v*7+t*.5)
            heat=(math.sin(v*19+t*2+math.sin(u*13))+1)/2
            blend=.5+.5*math.tanh((u-boundary)*12)
            c=dim(mix(a,b,blend),.25+.75*heat)
        elif effect=='Radar':
            dx,dy=u-.5,v-.5;theta=math.atan2(dy,dx);radius=math.hypot(dx,dy)
            tail=(t*.8-theta)%math.tau
            sweep=math.exp(-tail*2.5)*max(0,1-radius)
            rings=max(0,1-abs((radius*6)%1-.5)*18)*.15
            c=dim(mix(a,b,radius),min(1,sweep+rings))
        elif effect=='Kaleidoscope':
            radius=math.hypot(u-.5,v-.5);theta=math.atan2(v-.5,u-.5)
            petals=math.sin(theta*(4+int(density*8))+t*.4)*math.cos(radius*18-t)
            c=mix(a,b,(petals+1)/2)
        elif effect=='Audio bars':
            bands=inputs.get('bands') or [0]*8;band=min(len(bands)-1,max(0,int(u*len(bands))))
            level=min(1,max(0,bands[band]*settings.get('gain',4)))
            c=mix(a,b,1-v) if level>0 and 1-v<=level else (0,0,0)
        elif effect=='Audio rings':
            energy=min(1,inputs.get('audio',0)*settings.get('gain',4))
            radius=math.hypot(u-.5,v-.5)
            pulse=(math.sin(radius*24-t*3)+1)/2
            c=dim(mix(a,b,min(1,radius*2)),energy*pulse)
        else:
            age = max(0, elapsed-inputs.get('event_time',-100))
            c = mix((0,0,0),color(inputs.get('event_color','#FF6F91')),max(0,1-age/2))
        output.append(tuple(round(min(255,max(0,v*brightness))) for v in c))
    return output
