# SPDX-License-Identifier: GPL-3.0-only
"""Motherboard fan monitoring and speed control for ITE Super I/O chips.

Hardware access goes through the signed PawnIO driver and its LpcIO module
(both bundled with OpenRGB), which only permits the Super I/O configuration
ports and the chip's own register window. Register layout and the
save/override/restore sequence follow LibreHardwareMonitor (MPL-2.0,
Hardware/Motherboard/Lpc/IT87XX.cs).

Safety model:
  * Nothing is written until the user changes a header from "Auto (BIOS)".
  * The original register values are saved before the first write and are
    written back when a header returns to Auto, on Stop, on app exit, on any
    hardware error, and when the temperature failsafe trips.
  * Duty never goes below a floor (fans 20 %, pump 70 %).
  * Failsafe: if any board sensor reaches 85 °C, or sensors stop reading,
    every header goes back to BIOS control.
  * A controlled fan that stops spinning is handed back to BIOS control.
  * The second ITE chip on GIGABYTE boards is behind an extra embedded
    controller; it is monitored but never written.
"""
import ctypes
import sys
import threading
import time
from pathlib import Path

ISA_MUTEX = 'Global\\Access_ISABUS.HTP.Method'
ENTER_KEYS = {0: (0x87, 0x01, 0x55, 0x55), 1: (0x87, 0x01, 0x55, 0xAA)}
SLOT_PORT = {0: 0x2E, 1: 0x4E}
ITE_VENDOR_IDS = (0x90, 0x7F)
ADDRESS_OFFSET, DATA_OFFSET = 0x05, 0x06
VENDOR_ID_REGISTER = 0x58
CONFIGURATION_REGISTER = 0x00
FAN_MAIN_CTRL_REG = 0x13
FAN_TACH_16BIT_REG = 0x0C
FAN_PWM_CTRL_REG = (0x15, 0x16, 0x17, 0x7F, 0xA7, 0xAF)
FAN_PWM_CTRL_EXT_REG = (0x63, 0x6B, 0x73, 0x7B, 0xA3, 0xAB)
FAN_TACH_REG = (0x0D, 0x0E, 0x0F, 0x80, 0x82, 0x4C)
FAN_TACH_EXT_REG = (0x18, 0x19, 0x1A, 0x81, 0x83, 0x4D)
TEMPERATURE_BASE_REG = 0x29

# chip id: (name, fans, controllable headers, temperatures)
# Only chips whose PWM layout matches the default ITE register map with
# extended 8-bit duty registers are listed.
CHIPS = {
    0x8686: ('ITE IT8686E', 6, 5, 6),
    0x8688: ('ITE IT8688E', 6, 5, 6),
    0x8689: ('ITE IT8689E', 6, 6, 6),
    0x8696: ('ITE IT8696E', 6, 6, 6),
    0x8628: ('ITE IT8628E', 6, 6, 6),
    0x8695: ('ITE IT87952E', 3, 3, 3),
}

FAN_FLOOR = 20
PUMP_FLOOR = 70
FAILSAFE_C = 85
CURVES = {
    'Quiet':       ((30, 25), (50, 30), (65, 50), (75, 75), (82, 100)),
    'Balanced':    ((30, 30), (50, 40), (65, 60), (75, 85), (80, 100)),
    'Performance': ((30, 45), (45, 55), (60, 75), (70, 100)),
}
MODES = ('Auto (BIOS)', 'Fixed', *CURVES)


def curve_duty(curve, temperature):
    """Linear interpolation over (°C, %) points, clamped at both ends."""
    points = CURVES[curve]
    if temperature <= points[0][0]:
        return points[0][1]
    for (t0, d0), (t1, d1) in zip(points, points[1:]):
        if temperature <= t1:
            return round(d0 + (d1 - d0) * (temperature - t0) / (t1 - t0))
    return points[-1][1]


class FanError(Exception):
    pass


class PawnIOPort:
    """LpcIO access through PawnIO. Windows, administrator only."""

    def __init__(self, directory, module=None, mutex=ISA_MUTEX):
        if sys.platform != 'win32':
            raise FanError('Motherboard fan control is available on Windows only.')
        directory = Path(directory)
        try:
            self.lib = ctypes.WinDLL(str(directory / 'PawnIOLib.dll'))
            blob = Path(module or directory / 'LpcIO.bin').read_bytes()
        except OSError as e:
            raise FanError(f'PawnIO components are missing from the hardware engine: {e}') from None
        self.handle = ctypes.c_void_p()
        hr = self.lib.pawnio_open(ctypes.byref(self.handle)) & 0xFFFFFFFF
        if hr == 0x80070005:
            raise FanError('Hardware sensors and fan control need administrator rights. Start Prism Light with Run-PrismLight.cmd.')
        if hr:
            raise FanError(f'The PawnIO driver is not available (0x{hr:08X}). Run Install-Windows.cmd.')
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        hr = self.lib.pawnio_load(self.handle, buf, ctypes.c_size_t(len(blob))) & 0xFFFFFFFF
        if hr:
            self.close()
            raise FanError(f'Could not load the PawnIO LpcIO module (0x{hr:08X}).')
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel32 = kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        self.mutex = kernel32.CreateMutexW(None, False, mutex)

    def execute(self, name, args=(), out_count=0):
        args_buf = (ctypes.c_uint64 * max(1, len(args)))(*args)
        out_buf = (ctypes.c_uint64 * max(1, out_count))()
        returned = ctypes.c_size_t()
        hr = self.lib.pawnio_execute(self.handle, name.encode(), args_buf, ctypes.c_size_t(len(args)),
                                     out_buf, ctypes.c_size_t(out_count), ctypes.byref(returned)) & 0xFFFFFFFF
        if hr:
            raise FanError(f'Super I/O command {name} failed (0x{hr:08X}).')
        return out_buf[0] if out_count else None

    def lock(self):
        if self.mutex and self.kernel32.WaitForSingleObject(ctypes.c_void_p(self.mutex), 200) not in (0, 0x80):
            raise FanError('Another program is using the Super I/O chip. Try again.')

    def unlock(self):
        if self.mutex:
            self.kernel32.ReleaseMutex(ctypes.c_void_p(self.mutex))

    def select_slot(self, slot): self.execute('ioctl_select_slot', (slot,))
    def find_bars(self): self.execute('ioctl_find_bars')
    def superio_inb(self, reg): return self.execute('ioctl_superio_inb', (reg,), 1) & 0xFF
    def superio_outb(self, reg, value): self.execute('ioctl_superio_outb', (reg, value))
    def pio_inb(self, port): return self.execute('ioctl_pio_inb', (port,), 1) & 0xFF
    def pio_outb(self, port, value): self.execute('ioctl_pio_outb', (port, value))

    def close(self):
        if getattr(self, 'handle', None):
            self.lib.pawnio_close(self.handle)
            self.handle = None
        if getattr(self, 'mutex', None):
            self.kernel32.CloseHandle(ctypes.c_void_p(self.mutex))
            self.mutex = None


class ITEChip:
    def __init__(self, port, slot, chip_id, base, writable):
        self.port, self.slot, self.chip_id, self.base = port, slot, chip_id, base
        self.name, self.fan_count, self.control_count, self.temp_count = CHIPS[chip_id]
        self.writable = writable
        self.saved = {}  # header -> (ctrl, ext, main_bit)

    def read(self, reg):
        self.port.pio_outb(self.base + ADDRESS_OFFSET, reg)
        value = self.port.pio_inb(self.base + DATA_OFFSET)
        if self.port.pio_inb(self.base + ADDRESS_OFFSET) != reg and self.chip_id != 0x8688:
            raise FanError(f'{self.name} did not answer register 0x{reg:02X}.')
        return value

    def write(self, reg, value):
        self.port.pio_outb(self.base + ADDRESS_OFFSET, reg)
        self.port.pio_outb(self.base + DATA_OFFSET, value & 0xFF)
        self.port.pio_inb(self.base + ADDRESS_OFFSET)

    def validate(self):
        if self.read(VENDOR_ID_REGISTER) not in ITE_VENDOR_IDS:
            raise FanError(f'{self.name}: unexpected vendor ID.')
        if not self.read(CONFIGURATION_REGISTER) & 0x10:
            raise FanError(f'{self.name}: chip is not configured for monitoring.')

    def fans(self):
        modes = self.read(FAN_TACH_16BIT_REG)
        disabled = {3: not modes & 0x10, 4: not modes & 0x20, 5: not modes & 0x04}
        rpm = []
        for i in range(self.fan_count):
            if disabled.get(i):
                rpm.append(None)
                continue
            value = self.read(FAN_TACH_REG[i]) | self.read(FAN_TACH_EXT_REG[i]) << 8
            rpm.append(None if value <= 0x3F else 0 if value >= 0xFFFF else round(1.35e6 / (value * 2)))
        return rpm

    def temperatures(self):
        out = []
        for i in range(self.temp_count):
            value = self.read(TEMPERATURE_BASE_REG + i)
            value = value - 256 if value > 127 else value
            out.append(value if 0 < value < 127 else None)
        return out

    def duties(self):
        """(automatic, percent) per controllable header."""
        result = []
        for i in range(self.control_count):
            ctrl = self.read(FAN_PWM_CTRL_REG[i])
            ext = self.read(FAN_PWM_CTRL_EXT_REG[i])
            result.append((bool(ctrl & 0x80), round(ext * 100 / 255)))
        return result

    def set_duty(self, index, percent):
        if not self.writable:
            raise FanError(f'{self.name} is monitored only on this board.')
        if not 0 <= index < self.control_count:
            raise FanError('Unknown fan header.')
        value = round(max(0, min(100, percent)) * 255 / 100)
        if index not in self.saved:
            main = self.read(FAN_MAIN_CTRL_REG)
            self.saved[index] = (self.read(FAN_PWM_CTRL_REG[index]), self.read(FAN_PWM_CTRL_EXT_REG[index]),
                                 bool(main & (1 << index)) if index < 3 else None)
        ctrl, _ext, main_bit = self.saved[index]
        if index < 3 and not main_bit:
            self.write(FAN_MAIN_CTRL_REG, self.read(FAN_MAIN_CTRL_REG) | (1 << index))
        self.write(FAN_PWM_CTRL_REG[index], 0x7F if self.chip_id == 0x8689 else ctrl & 0x7F)
        self.write(FAN_PWM_CTRL_EXT_REG[index], value)

    def restore(self, index):
        if index not in self.saved:
            return
        ctrl, ext, main_bit = self.saved.pop(index)
        self.write(FAN_PWM_CTRL_REG[index], ctrl)
        if index < 3:
            main = self.read(FAN_MAIN_CTRL_REG)
            if bool(main & (1 << index)) != main_bit:
                self.write(FAN_MAIN_CTRL_REG, main ^ (1 << index))
        self.write(FAN_PWM_CTRL_EXT_REG[index], ext)

    def restore_all(self):
        for index in list(self.saved):
            self.restore(index)


def leave(port, slot):
    # Like LibreHardwareMonitor, never exit config mode on the secondary chip.
    if slot == 0:
        port.superio_outb(0x02, 0x02)


def detect(make_port, gigabyte=True):
    """Find supported ITE chips, each with its own PawnIO connection.

    The LpcIO module forgets its allowed register windows whenever a slot is
    selected, so one shared connection would lock out the first chip as soon
    as the second slot is probed. Each chip therefore keeps its own handle.
    """
    chips, notes = [], []
    for slot in (0, 1):
        port = make_port()
        kept = False
        port.lock()
        try:
            port.select_slot(slot)
            reg = SLOT_PORT[slot]
            for key in ENTER_KEYS[slot]:
                port.pio_outb(reg, key)
            chip_id = port.superio_inb(0x20) << 8 | port.superio_inb(0x21)
            if chip_id in (0, 0xFFFF):
                continue
            if chip_id not in CHIPS:
                notes.append(f'Super I/O 0x{chip_id:04X} at 0x{reg:02X} is not supported for fan control yet.')
                leave(port, slot)
                continue
            port.find_bars()
            port.superio_outb(0x07, 0x04)  # environment controller
            base = port.superio_inb(0x60) << 8 | port.superio_inb(0x61)
            leave(port, slot)
            if base < 0x100 or base & 0xF007:
                notes.append(f'{CHIPS[chip_id][0]}: invalid register address.')
                continue
            writable = not (gigabyte and slot == 1)
            chip = ITEChip(port, slot, chip_id, base, writable)
            chip.validate()
            chips.append(chip)
            kept = True
        except FanError as e:
            notes.append(str(e))
        finally:
            port.unlock()
            if not kept and hasattr(port, 'close'):
                port.close()
    return chips, notes


def board_vendor():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\BIOS') as key:
            return winreg.QueryValueEx(key, 'BaseBoardManufacturer')[0]
    except (OSError, ImportError):
        return ''


class Header:
    def __init__(self, chip, index):
        self.chip, self.index = chip, index
        self.label = f'{chip.name} · fan {index + 1}'
        self.mode = 'Auto (BIOS)'
        self.fixed = 60
        self.pump = False
        self.rpm = None
        self.duty = None
        self.automatic = True
        self.target = None
        self.zero_since = None
        self.note = ''

    @property
    def key(self):
        return f'{self.chip.chip_id:04X}:{self.index}'

    @property
    def floor(self):
        return PUMP_FLOOR if self.pump else FAN_FLOOR


class FanService:
    """Owns the fan chips on a background thread (one-second control loop)."""
    interval = 1.0
    stall_seconds = 5

    def __init__(self, opener=None):
        self.opener = opener
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.thread = None
        self.port = None
        self.chips = []
        self.headers = []
        self.extra_fans = []
        self.temperatures = []
        self.status = 'Fan control is off. Fans follow the BIOS.'
        self.notes = []
        self.failsafe = None
        self.preset = {}

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def settings(self):
        """Per-header settings worth remembering between sessions."""
        with self.lock:
            return {h.key: {'mode': h.mode, 'fixed': h.fixed, 'pump': h.pump} for h in self.headers}

    def start(self, preset=None):
        if self.running:
            return
        self.preset = dict(preset or {})
        self.stop_flag.clear()
        self.failsafe = None
        self.status = 'Connecting to the motherboard fan controller…'
        self.thread = threading.Thread(target=self.run, name='fan-control', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        if self.thread:
            self.thread.join(5)
        self.thread = None

    def configure(self, key, **changes):
        with self.lock:
            for h in self.headers:
                if h.key == key:
                    for name, value in changes.items():
                        setattr(h, name, value)
                    h.note = ''
                    self.failsafe = None

    def snapshot(self):
        with self.lock:
            return {
                'status': self.status, 'notes': list(self.notes), 'failsafe': self.failsafe,
                'temperatures': list(self.temperatures),
                'headers': [dict(key=h.key, label=h.label, mode=h.mode, fixed=h.fixed, pump=h.pump, rpm=h.rpm,
                                 duty=h.duty, automatic=h.automatic, target=h.target, note=h.note,
                                 writable=h.chip.writable) for h in self.headers],
                'extra_fans': list(self.extra_fans),
            }

    def open(self):
        if self.opener:
            return self.opener()
        from .hardware import Hardware
        directory = Hardware().executable.parent
        return detect(lambda: PawnIOPort(directory), gigabyte='gigabyte' in board_vendor().casefold())

    def run(self):
        try:
            self.chips, notes = self.open()
        except Exception as e:
            with self.lock:
                self.status = f'Fan control unavailable: {e}'
            return
        with self.lock:
            self.notes = notes
            self.headers = [Header(c, i) for c in self.chips for i in range(c.control_count)]
            # Saved settings from the last session (mode, fixed %, pump).
            for h in self.headers:
                saved = self.preset.get(h.key, {}) if h.chip.writable else {}
                if saved.get('mode') in MODES:
                    h.mode = saved['mode']
                if isinstance(saved.get('fixed'), int):
                    h.fixed = max(0, min(100, saved['fixed']))
                if isinstance(saved.get('pump'), bool):
                    h.pump = saved['pump']
            if not self.chips:
                self.status = 'No supported motherboard fan controller was found.' + (' ' + ' '.join(notes) if notes else '')
        try:
            while self.chips and not self.stop_flag.is_set():
                self.cycle()
                self.stop_flag.wait(self.interval)
        except Exception as e:
            with self.lock:
                self.status = f'Fan control stopped after a hardware error: {e} Fans were returned to the BIOS.'
        finally:
            self.release()

    def cycle(self):
        temps, extra = [], []
        readings = {}
        for chip in self.chips:
            chip.port.lock()
            try:
                temps.extend(t for t in chip.temperatures() if t is not None)
                rpm = chip.fans()
                duties = chip.duties()
            finally:
                chip.port.unlock()
            readings[chip] = (rpm, duties)
            extra.extend((f'{chip.name} · fan {i + 1}', r) for i, r in enumerate(rpm[chip.control_count:]))
        hottest = max(temps) if temps else None
        with self.lock:
            self.temperatures = temps
            self.extra_fans = extra
            headers = list(self.headers)
            for h in headers:
                rpm, duties = readings[h.chip]
                h.rpm = rpm[h.index]
                h.automatic, h.duty = duties[h.index]
        if hottest is None or hottest >= FAILSAFE_C:
            reason = 'board sensors stopped reporting' if hottest is None else f'a board sensor reached {hottest} °C'
            if any(h.mode != 'Auto (BIOS)' for h in headers):
                self.hand_back_all(f'Failsafe: {reason}. All fans returned to BIOS control.')
            return
        now = time.monotonic()
        for h in headers:
            if h.mode == 'Auto (BIOS)' or not h.chip.writable:
                if h.index in h.chip.saved:
                    self.restore(h)
                h.target = None
                continue
            target = h.fixed if h.mode == 'Fixed' else curve_duty(h.mode, hottest)
            target = max(h.floor, min(100, target))
            # A fan that was spinning and now reads 0 RPM while driven is
            # stalled or unplugged: give it back to the BIOS.
            if h.rpm == 0 and h.target is not None:
                h.zero_since = h.zero_since or now
                if now - h.zero_since > self.stall_seconds:
                    self.restore(h)
                    h.mode, h.target, h.zero_since = 'Auto (BIOS)', None, None
                    h.note = 'Stopped spinning while controlled; returned to BIOS control.'
                    continue
            else:
                h.zero_since = None
            h.chip.port.lock()
            try:
                h.chip.set_duty(h.index, target)
            finally:
                h.chip.port.unlock()
            h.target = target
        controlled = sum(h.target is not None for h in headers)
        with self.lock:
            self.status = (f'Fan control active · {controlled} header(s) controlled · hottest sensor {hottest} °C'
                           if controlled else f'Monitoring · all fans follow the BIOS · hottest sensor {hottest} °C')

    def restore(self, header):
        header.chip.port.lock()
        try:
            header.chip.restore(header.index)
        finally:
            header.chip.port.unlock()

    def hand_back_all(self, reason):
        for chip in self.chips:
            chip.port.lock()
            try:
                chip.restore_all()
            finally:
                chip.port.unlock()
        with self.lock:
            for h in self.headers:
                h.mode, h.target = 'Auto (BIOS)', None
            self.failsafe = reason
            self.status = reason

    def release(self):
        try:
            for chip in self.chips:
                if chip.saved:
                    chip.port.lock()
                    try:
                        chip.restore_all()
                    finally:
                        chip.port.unlock()
        except Exception:
            pass
        finally:
            for chip in self.chips:
                if hasattr(chip.port, 'close'):
                    chip.port.close()
            with self.lock:
                if not self.status.startswith(('Fan control stopped', 'Fan control unavailable', 'Failsafe')):
                    self.status = 'Fan control is off. Fans follow the BIOS.'
