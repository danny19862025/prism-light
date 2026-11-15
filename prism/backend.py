# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Prism Light contributors
"""Original, bounded OpenRGB SDK v3 client. No USB/driver access."""
from dataclasses import dataclass, field
import hashlib
import json
import select
import socket
import struct
import time

class SDKError(Exception):
    pass

@dataclass
class Device:
    index: int
    name: str
    vendor: str
    serial: str
    location: str
    zones: list
    leds: int
    modes: list
    active_mode: int = 0
    zone_limits: list = field(default_factory=list)  # (min, max) LEDs per zone

    @property
    def resizable(self):
        """Zones whose LED count the user sets, e.g. motherboard ARGB headers."""
        return [(i, title, leds, lo, hi) for i, ((title, leds), (lo, hi))
                in enumerate(zip(self.zones, self.zone_limits)) if hi > lo]

    @property
    def key(self):
        return hashlib.sha256(json.dumps([self.name, self.vendor, self.serial, self.location]).encode()).hexdigest()

    @property
    def direct(self):
        # Only explicit direct modes; arbitrary animation modes are not safe fallbacks.
        return next((i for i, m in enumerate(self.modes)
                     if m[0].casefold() in ('direct', 'custom') and m[1][1] & 32), None)

    @property
    def supported(self):
        return self.direct is not None and self.leds > 0

class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0
    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise SDKError('Malformed SDK data: truncated field.')
        out = self.data[self.pos:self.pos+n]
        self.pos += n
        return out
    def unpack(self, fmt):
        return struct.unpack('<'+fmt, self.take(struct.calcsize('<'+fmt)))
    def number(self, fmt='I'):
        return self.unpack(fmt)[0]
    def string(self):
        raw = self.take(self.number('H'))
        if not raw or raw[-1] != 0:
            raise SDKError('Malformed SDK string.')
        return raw[:-1].decode('utf-8', errors='replace')

def parse_device(index, data):
    r = Reader(data)
    if r.number() != len(data):
        raise SDKError('Invalid SDK device block length.')
    r.number()  # lighting device type only
    name, vendor, _desc, _version, serial, location = [r.string() for _ in range(6)]
    count = r.number('H')
    active_mode = r.number()
    modes = []
    for _ in range(count):
        title = r.string()
        fields = list(r.unpack('12I'))
        colors = r.take(4*r.number('H'))
        modes.append((title, fields, colors))
    zones, limits = [], []
    for _ in range(r.number('H')):
        title = r.string()
        _type, low, high, leds = r.unpack('4I')
        r.take(r.number('H'))  # optional matrix map
        zones.append((title, leds))
        limits.append((low, high))
    leds = r.number('H')
    for _ in range(leds):
        r.string()
        r.number()
    colors = r.number('H')
    r.take(colors*4)
    if r.pos != len(data):
        raise SDKError('Unexpected SDK v3 device data.')
    return Device(index, name, vendor, serial, location, zones, min(leds, colors), modes, active_mode, limits)

def check_resize(devices, device_key, zone, size):
    device = next((d for d in devices if d.key == device_key), None)
    if device is None:
        raise SDKError('That device is no longer connected. Scan devices again.')
    match = next((z for z in device.resizable if z[0] == zone), None)
    if match is None:
        raise ValueError('This zone has a fixed LED count.')
    if type(size) is not int or not match[3] <= size <= match[4]:
        raise ValueError(f'{match[1]} accepts {match[3]}–{match[4]} LEDs.')
    return device

def rgb_bytes(color, brightness):
    if not isinstance(color, str) or len(color) != 7 or not color.startswith('#'):
        raise ValueError('Use a colour in #RRGGBB format.')
    if type(brightness) is not int or not 0 <= brightness <= 100:
        raise ValueError('Brightness must be between 0 and 100.')
    values = [int(color[i:i+2], 16) for i in (1, 3, 5)]
    return bytes(round(v*brightness/100) for v in values) + b'\0'

class OpenRGB:
    demo = False
    def __init__(self):
        self.sock = None
        self.devices = []
        self.prepared = set()
        self.list_changed = False
    def close(self):
        if self.sock:
            self.sock.close()
        self.sock = None
        self.devices = []
        self.prepared = set()
        self.list_changed = False
    def send(self, command, data=b'', device=0):
        if not self.sock:
            raise SDKError('Disconnected. Connect to OpenRGB first.')
        self.sock.sendall(struct.pack('<4sIII', b'ORGB', device, command, len(data))+data)
    def exact(self, n):
        result = bytearray()
        deadline = time.monotonic()+3
        while len(result) < n:
            self.sock.settimeout(max(.01, deadline-time.monotonic()))
            part = self.sock.recv(n-len(result))
            if not part:
                raise SDKError('OpenRGB closed the connection. Restart its SDK server and reconnect.')
            result.extend(part)
            if time.monotonic() > deadline:
                raise SDKError('OpenRGB response timed out.')
        return bytes(result)
    def receive(self):
        while True:
            magic, device, command, size = struct.unpack('<4sIII', self.exact(16))
            if magic != b'ORGB' or size > 8*1024*1024:
                raise SDKError('Invalid SDK packet. Check that port 6742 belongs to OpenRGB.')
            data = self.exact(size)
            if command == 100:
                # Unsolicited notice (USB re-enumeration, hotplug, wake from
                # sleep). Record it; health() re-reads the list before writes.
                self.list_changed = True
                continue
            return device, command, data
    def request(self, command, data=b'', device=0):
        self.send(command, data, device)
        got_device, got_command, response = self.receive()
        if (got_command, got_device) != (command, device):
            raise SDKError('Unexpected SDK response. Reconnect to refresh devices.')
        return response
    def connect(self, port=6742):
        self.close()
        try:
            if not 1 <= port <= 65535:
                raise ValueError('Port must be between 1 and 65535.')
            self.port = port
            self.sock = socket.create_connection(('127.0.0.1', port), timeout=3)
            response = self.request(40, struct.pack('<I', 3))
            if len(response) != 4 or struct.unpack('<I', response)[0] < 3:
                raise SDKError('This app needs OpenRGB SDK protocol 3 or later (OpenRGB 0.7+).')
            self.send(50, b'Prism Light\0')
            raw = self.request(0)
            if len(raw) != 4:
                raise SDKError('Invalid SDK controller count.')
            count = struct.unpack('<I', raw)[0]
            if count > 256:
                raise SDKError('Too many controllers in SDK response.')
            self.devices = [parse_device(i, self.request(1, struct.pack('<I', 3), i)) for i in range(count)]
            return self.devices
        except Exception:
            self.close()
            raise
    def health(self):
        raw = self.request(0)
        if raw != struct.pack('<I', len(self.devices)):
            raise SDKError('Device count changed. Reconnect to refresh devices.')
        if self.list_changed:
            self.refresh_devices()

    def refresh_devices(self):
        """Re-read devices after a change notice; continue only if identical."""
        current = [parse_device(i, self.request(1, struct.pack('<I', 3), i)) for i in range(len(self.devices))]
        if [(d.key, d.leds) for d in current] != [(d.key, d.leds) for d in self.devices]:
            raise SDKError('OpenRGB device list changed. Reconnect before applying lighting.')
        self.devices = current
        # Controllers may have been re-created and left Direct mode.
        self.prepared = set()
        self.list_changed = False
    def apply(self, keys, color, brightness):
        pixel = rgb_bytes(color, brightness)
        self.health()  # consumes pending topology notices before sending indexed writes
        results = []
        for device in self.devices:
            if device.key not in keys:
                continue
            if not device.supported:
                results.append(device.name + ': skipped (no supported direct colour mode)')
                continue
            self.prepare(device)
            body = struct.pack('<H', device.leds)+pixel*device.leds
            self.send(1050, struct.pack('<I', len(body)+4)+body, device.index)
            results.append(device.name + ': sent')
        return '; '.join(results) or 'No matching devices selected.'

    def resize_zone(self, device_key, zone, size):
        return self.resize_zones([(device_key, zone, size)])

    def resize_zones(self, changes):
        """Set how many LEDs are on resizable zones, then reload devices.

        OpenRGB saves the sizes in its own configuration, so this is a one-time
        step per header. Indices change meaning after a resize: reconnect."""
        checked = [(check_resize(self.devices, k, z, n), z, n) for k, z, n in changes]
        for device, zone, size in checked:
            self.send(1000, struct.pack('<ii', zone, size), device.index)
        time.sleep(.3)
        return self.connect(self.port)

    def prepare(self, device):
        # Let OpenRGB perform the driver's native transition into software
        # control, including its per-zone active-mode handling.
        self.send(1100, device=device.index)
        title, fields, colors = device.modes[device.direct]
        fields = fields.copy()
        fields[11] = 1
        if fields[1] & 16:
            fields[9] = fields[5]
        title = title.encode('utf-8') + b'\0'
        mode = struct.pack('<H', len(title)) + title + struct.pack('<12I', *fields) + struct.pack('<H', len(colors)//4) + colors
        body = struct.pack('<I', device.direct) + mode
        self.send(1101, struct.pack('<I', len(body)+4) + body, device.index)
        current = parse_device(device.index, self.request(1, struct.pack('<I', 3), device.index))
        if current.key != device.key or current.leds != device.leds:
            raise SDKError('Device topology changed while enabling software control. Scan devices again.')
        if current.active_mode != device.direct or current.modes[device.direct][1][11] != 1:
            raise SDKError(device.name + ': OpenRGB did not activate Direct mode. Close other RGB applications and scan again.')
        self.prepared.add(device.key)

    def apply_frame(self, frames):
        """Send complete per-device RGB frames. Called only by the serial worker."""
        from collections import Counter
        counts = Counter(d.key for d in self.devices)
        encoded = []
        # Validate the entire frame before any mode or lighting mutation.
        for device in self.devices:
            if device.key not in frames:
                continue
            if not device.supported or counts[device.key] != 1:
                raise SDKError('Frame targets an unsupported or ambiguous device.')
            pixels = frames[device.key]
            if len(pixels) != device.leds:
                raise ValueError('Frame LED count does not match the connected device.')
            data = bytearray()
            for pixel in pixels:
                if len(pixel) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in pixel):
                    raise ValueError('Frame pixels must contain three integer channels, 0–255.')
                data.extend((*pixel, 0))
            encoded.append((device, bytes(data)))
        if set(frames) - {d.key for d in self.devices}:
            raise SDKError('Frame targets a missing device. Reconnect to refresh the layout.')
        self.health()
        for device, pixels in encoded:
            if device.key not in self.prepared:
                self.prepare(device)
            body = struct.pack('<H', device.leds) + pixels
            self.send(1050, struct.pack('<I', len(body)+4) + body, device.index)
        return len(encoded)

class Demo:
    demo = True
    def __init__(self):
        mode = [('Direct', [0,32,0,0,0,100,0,0,0,100,0,1], b'')]
        self.devices = [Device(0,'Studio keyboard','Simulated','demo-1','demo',[('Main keys', 87),('Edge light',12)],99,mode),
                        Device(1,'Desk light strip','Simulated','demo-2','demo',[('Ambient strip',30)],30,mode,0,[(0,300)]),
                        Device(2,'Legacy accent light','Simulated','demo-3','demo',[('Accent',1)],1,[])]
    def connect(self, port=6742):
        return self.devices
    def resize_zone(self, device_key, zone, size):
        return self.resize_zones([(device_key, zone, size)])
    def resize_zones(self, changes):
        for key, zone, size in changes:
            d = check_resize(self.devices, key, zone, size)
            d.zones = [(t, size if i == zone else n) for i, (t, n) in enumerate(d.zones)]
            d.leds = sum(n for _, n in d.zones)
        return self.devices
    def close(self):
        pass
    def health(self):
        pass
    def apply(self, keys, color, brightness):
        rgb_bytes(color, brightness)
        count = sum(d.key in keys and d.supported for d in self.devices)
        return f'DEMO: simulated colour on {count} device(s). No hardware commands sent.'

    def apply_frame(self, frames):
        self.last_frame = frames
        return len(frames)
