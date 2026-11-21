# SPDX-License-Identifier: GPL-3.0-only
"""Corsair Nautilus LCD Cap (AIO pump screen) driver.

The cap is a 480x480 display that accepts JPEG images over USB HID. Protocol
as documented by OpenLinkHub (GPL-3.0, src/devices/nautilusLcd):
  * output report 0x02, command 0x05, 1024 bytes per packet
  * byte 3 = 1 on the final packet of an image (tells the screen to draw)
  * byte 4 = packet index, bytes 6-7 = payload length (little endian)
  * feature reports 03 0b <brightness> 01 and 03 0c <rotation> 01
  * 03 1e 01 01 then 03 1d 00 01 return the screen to its built-in display
Only display commands are sent: no firmware, pump or fan writes.
"""
import struct
import threading
import time

VENDOR = 0x1B1C
PRODUCTS = {0x0C57: 'CORSAIR Nautilus LCD Cap', 0x0C55: 'CORSAIR Nautilus LCD Cap'}
SIZE = 480
PACKET = 1024
HEADER = 8
PAYLOAD = PACKET - HEADER
ROTATIONS = (0, 90, 180, 270)


def packets(jpeg):
    """Split one JPEG into display packets; the last one is flagged."""
    if not jpeg:
        raise ValueError('Empty image.')
    chunks = [jpeg[i:i+PAYLOAD] for i in range(0, len(jpeg), PAYLOAD)]
    if len(chunks) > 256:
        raise ValueError('Image too large for the display.')
    out = []
    for index, chunk in enumerate(chunks):
        pkt = bytearray(PACKET)
        pkt[0], pkt[1] = 0x02, 0x05
        pkt[3] = 1 if index == len(chunks) - 1 else 0
        pkt[4] = index
        pkt[6:8] = struct.pack('<H', len(chunk))
        pkt[8:8+len(chunk)] = chunk
        out.append(bytes(pkt))
    return out


def find():
    """Return (hid module, device info) for an attached cap, or (None, reason)."""
    try:
        import hid
    except ImportError:
        return None, 'USB display support is not installed (Python package hidapi).'
    try:
        for info in hid.enumerate(VENDOR):
            if info['product_id'] in PRODUCTS and info.get('interface_number', 0) in (0, -1):
                return hid, info
    except Exception as e:  # driver-level enumeration failure
        return None, f'USB enumeration failed: {e}'
    return None, 'No supported AIO display found.'


class NautilusLCD:
    def __init__(self, hid_module, info):
        self.info = info
        self.name = PRODUCTS[info['product_id']]
        self.dev = hid_module.device()
        self.dev.open_path(info['path'])
    def send_jpeg(self, jpeg):
        for pkt in packets(jpeg):
            if self.dev.write(pkt) < 0:
                raise OSError(f'Display write failed: {self.dev.error()}')
    def feature(self, report):
        if self.dev.send_feature_report(bytes(report)) < 0:
            raise OSError(f'Display setting failed: {self.dev.error()}')
    def set_brightness(self, percent):
        self.feature([0x03, 0x0B, max(0, min(100, int(percent))), 0x01])
    def set_rotation(self, degrees):
        self.feature([0x03, 0x0C, ROTATIONS.index(degrees), 0x01])
    def close(self):
        try:
            # Hand the screen back to its built-in display.
            self.feature([0x03, 0x1E, 0x01, 0x01])
            self.feature([0x03, 0x1D, 0x00, 0x01])
        except OSError:
            pass
        finally:
            self.dev.close()


class DisplayWorker:
    """Owns the display on a background thread.

    The GUI submits frames; the newest one wins. The current frame is resent
    once a second so the screen does not fall back to its built-in display.
    """
    keepalive = 1.0

    def __init__(self, opener=None):
        self.opener = opener or self.open_default
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop_flag = threading.Event()
        self.frame = None
        self.settings = {}
        self.status = 'AIO display is off.'
        self.error = None
        self.name = None
        self.frames_sent = 0
        self.thread = None

    @staticmethod
    def open_default():
        hid, info = find()
        if hid is None:
            raise OSError(info)
        return NautilusLCD(hid, info)

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, brightness=100, rotation=0):
        if self.running:
            return
        self.stop_flag.clear()
        self.error = None
        self.frames_sent = 0
        self.settings = {'brightness': brightness, 'rotation': rotation}
        self.status = 'Connecting to AIO display…'
        self.thread = threading.Thread(target=self.run, name='aio-display', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        self.wake.set()
        if self.thread:
            self.thread.join(3)
        self.thread = None
        if not self.error:
            self.status = 'AIO display is off. It shows its built-in screen.'

    def submit(self, jpeg):
        with self.lock:
            self.frame = jpeg
        self.wake.set()

    def configure(self, **settings):
        with self.lock:
            self.settings.update(settings)
            self.settings['dirty'] = True
        self.wake.set()

    def run(self):
        try:
            screen = self.opener()
        except Exception as e:
            self.error = str(e)
            self.status = f'AIO display unavailable: {e}'
            return
        self.name = screen.name
        try:
            applied = {}
            last = 0
            sent = None
            while not self.stop_flag.is_set():
                with self.lock:
                    frame = self.frame
                    settings = dict(self.settings)
                    self.settings.pop('dirty', None)
                if settings.get('brightness') != applied.get('brightness'):
                    screen.set_brightness(settings['brightness'])
                    applied['brightness'] = settings['brightness']
                if settings.get('rotation') != applied.get('rotation'):
                    screen.set_rotation(settings['rotation'])
                    applied['rotation'] = settings['rotation']
                now = time.monotonic()
                if frame and (frame is not sent or now - last >= self.keepalive):
                    screen.send_jpeg(frame)
                    sent, last = frame, now
                    self.frames_sent += 1
                    self.status = f'{screen.name} · showing Prism Light · {self.frames_sent} frames'
                self.wake.wait(.1)
                self.wake.clear()
        except Exception as e:
            self.error = str(e)
            self.status = f'AIO display stopped: {e} Close iCUE/SignalRGB if they are running, then press Start again.'
        finally:
            screen.close()
