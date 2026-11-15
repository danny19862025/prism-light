# SPDX-License-Identifier: GPL-3.0-only
import time
import unittest
from prism import fans
from prism.fans import (detect, FanService, curve_duty, FanError, FAN_PWM_CTRL_REG, FAN_PWM_CTRL_EXT_REG,
                        FAN_MAIN_CTRL_REG, FAN_TACH_REG, FAN_TACH_EXT_REG, TEMPERATURE_BASE_REG, PUMP_FLOOR, FAN_FLOOR)


class FakeITE:
    """Register model of an ITE IT8696E environment controller."""
    def __init__(self, chip_id=0x8696, base=0x0A40):
        self.chip_id, self.base = chip_id, base
        self.regs = {0x58: 0x90, 0x00: 0x13, 0x0C: 0x37, FAN_MAIN_CTRL_REG: 0x00}
        for i, reg in enumerate(FAN_PWM_CTRL_REG):
            self.regs[reg] = 0x80 | i          # BIOS SmartGuardian (automatic)
            self.regs[FAN_PWM_CTRL_EXT_REG[i]] = 100
        self.set_rpm([1350, 900, 0, 675, 2700, 1500])
        self.set_temps([40, 45, 52, 38, 50, 33])
        self.writes = []
    def set_rpm(self, rpms):
        for i, rpm in enumerate(rpms):
            count = 0xFFFF if rpm == 0 else round(1.35e6 / (rpm * 2))
            self.regs[FAN_TACH_REG[i]] = count & 0xFF
            self.regs[FAN_TACH_EXT_REG[i]] = count >> 8
    def set_temps(self, temps):
        for i, t in enumerate(temps):
            self.regs[TEMPERATURE_BASE_REG + i] = t & 0xFF


class FakeBus:
    """Shared hardware: chips per slot and their config-mode state."""
    def __init__(self, slots):
        self.slots = slots          # slot -> FakeITE or None
        self.entered = {}
        self.ldn = {}
        self.address = {}
        self.fail_after = None
        self.calls = 0
        self.connections = []
    def connect(self):
        port = FakePort(self)
        self.connections.append(port)
        return port


class FakePort:
    """One PawnIO connection. Like the LpcIO module, selecting a slot wipes
    the allowed register windows, and find_bars only allows the current chip."""
    def __init__(self, bus):
        self.bus = bus
        self.slot = None
        self.allowed = set()
        self.keys = []
        self.locked = 0
        self.closed = False
    def tick(self):
        self.bus.calls += 1
        if self.bus.fail_after is not None and self.bus.calls > self.bus.fail_after:
            raise FanError('simulated bus error')
    def lock(self): self.locked += 1
    def unlock(self): self.locked -= 1
    def close(self): self.closed = True
    def select_slot(self, slot): self.slot = slot; self.keys = []; self.allowed = set()
    def chip(self): return self.bus.slots.get(self.slot)
    def find_bars(self):
        if self.chip() and self.bus.entered.get(self.slot): self.allowed.add(self.chip().base)
    def superio_inb(self, reg):
        chip = self.chip()
        if not chip or not self.bus.entered.get(self.slot): return 0xFF
        if reg == 0x20: return chip.chip_id >> 8
        if reg == 0x21: return chip.chip_id & 0xFF
        if self.bus.ldn.get(self.slot) == 4 and reg in (0x60, 0x61):
            return chip.base >> 8 if reg == 0x60 else chip.base & 0xFF
        return 0
    def superio_outb(self, reg, value):
        if reg == 0x07: self.bus.ldn[self.slot] = value
        if reg == 0x02 and value == 0x02: self.bus.entered[self.slot] = False
    def window(self, port):
        for chip in self.bus.slots.values():
            if chip and chip.base <= port < chip.base + 8:
                if chip.base not in self.allowed:
                    raise FanError('Super I/O command ioctl_pio_outb failed (0x80070005).')
                return chip
        raise AssertionError(f'port {port:x} outside every chip window')
    def pio_outb(self, port, value):
        self.tick()
        if port in (0x2E, 0x4E):
            self.keys.append(value)
            if self.keys[-4:] == [0x87, 0x01, 0x55, 0x55 if port == 0x2E else 0xAA]:
                self.bus.entered[self.slot] = True
            return
        chip = self.window(port)
        if port == chip.base + 5: self.bus.address[chip.base] = value
        elif port == chip.base + 6:
            reg = self.bus.address.get(chip.base, 0); chip.regs[reg] = value; chip.writes.append((reg, value))
        else: raise AssertionError(f'port {port:x} outside the chip registers')
    def pio_inb(self, port):
        self.tick()
        chip = self.window(port)
        if port == chip.base + 5: return self.bus.address.get(chip.base, 0)
        if port == chip.base + 6: return chip.regs.get(self.bus.address.get(chip.base, 0), 0)
        raise AssertionError(f'port {port:x} outside the chip registers')


def service_for(bus, gigabyte=True):
    svc = FanService(lambda: detect(bus.connect, gigabyte))
    svc.interval = .05; svc.stall_seconds = .2
    return svc


def wait(cond, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond(): time.sleep(.02)
    return cond()


class CurveTests(unittest.TestCase):
    def test_interpolates_and_clamps(self):
        self.assertEqual(curve_duty('Balanced', 20), 30)
        self.assertEqual(curve_duty('Balanced', 57.5), 50)
        self.assertEqual(curve_duty('Balanced', 95), 100)


class DetectTests(unittest.TestCase):
    def test_detects_main_and_secondary_and_protects_gigabyte_secondary(self):
        main, second = FakeITE(), FakeITE(0x8695, 0x0A60)
        bus = FakeBus({0: main, 1: second})
        chips, notes = detect(bus.connect, gigabyte=True)
        self.assertEqual([c.name for c in chips], ['ITE IT8696E', 'ITE IT87952E'])
        self.assertEqual([c.writable for c in chips], [True, False])
        self.assertFalse(bus.entered[0])         # main chip left config mode
        self.assertTrue(bus.entered[1])          # secondary deliberately not exited
        self.assertTrue(all(c.locked == 0 for c in bus.connections))
        self.assertIsNot(chips[0].port, chips[1].port)   # each chip keeps its own connection
        self.assertEqual(chips[0].fans()[:3], [1350, 900, 0])
        self.assertEqual(chips[0].temperatures()[:3], [40, 45, 52])
        self.assertTrue(all(auto for auto, _ in chips[0].duties()))
        with self.assertRaises(FanError):chips[1].set_duty(0, 50)
    def test_unsupported_chip_is_reported_not_driven(self):
        bus = FakeBus({0: FakeITE(0x8613)})
        chips, notes = detect(bus.connect)
        self.assertEqual(chips, []); self.assertIn('not supported', notes[0])
        self.assertTrue(all(c.closed for c in bus.connections))   # unused connections released
    def test_main_chip_stays_readable_after_probing_second_slot(self):
        # Regression: one shared connection lost the first chip's window
        # when slot 1 was selected (ioctl_pio_outb 0x80070005 on real PCs).
        for second in (None, FakeITE(0x8695, 0x0A60), FakeITE(0x8613, 0x0A60)):
            bus = FakeBus({0: FakeITE(), 1: second})
            chips, _ = detect(bus.connect)
            self.assertEqual(chips[0].fans()[0], 1350)
            chips[0].set_duty(0, 50)


class ChipWriteTests(unittest.TestCase):
    def setUp(self):
        self.ite = FakeITE(); self.bus = FakeBus({0: self.ite})
        self.chip = detect(self.bus.connect)[0][0]; self.original = dict(self.ite.regs)
    def test_set_duty_then_restore_returns_every_register(self):
        self.chip.set_duty(1, 50)
        self.assertEqual(self.ite.regs[FAN_PWM_CTRL_EXT_REG[1]], 128)
        self.assertFalse(self.ite.regs[FAN_PWM_CTRL_REG[1]] & 0x80)       # manual mode
        self.assertTrue(self.ite.regs[FAN_MAIN_CTRL_REG] & 0x02)           # output enabled
        self.chip.set_duty(1, 80)                                            # saved once only
        self.chip.restore(1)
        self.assertEqual(self.ite.regs, self.original)
    def test_writes_stay_on_requested_header(self):
        self.chip.set_duty(4, 40)
        touched = {reg for reg, _ in self.ite.writes}
        self.assertEqual(touched, {FAN_PWM_CTRL_REG[4], FAN_PWM_CTRL_EXT_REG[4]})


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.ite = FakeITE(); self.bus = FakeBus({0: self.ite, 1: FakeITE(0x8695, 0x0A60)})
        self.original = dict(self.ite.regs); self.svc = service_for(self.bus)
    def tearDown(self): self.svc.stop()
    def header(self, i): return self.svc.snapshot()['headers'][i]
    def test_monitor_only_until_user_changes_a_header(self):
        self.svc.start()
        self.assertTrue(wait(lambda: self.svc.snapshot()['headers'] and self.header(0)['rpm'] == 1350))
        time.sleep(.2)
        self.assertEqual(self.ite.writes, [])
        self.assertIn('Monitoring', self.svc.snapshot()['status'])
    def test_fixed_and_curve_respect_floors_and_stop_restores(self):
        self.svc.start(); wait(lambda: self.svc.snapshot()['headers'])
        self.svc.configure('8696:0', mode='Fixed', fixed=5)
        self.svc.configure('8696:1', mode='Balanced', pump=True)
        self.assertTrue(wait(lambda: self.header(0)['target'] == FAN_FLOOR and self.header(1)['target'] == PUMP_FLOOR))
        self.svc.configure('8696:0', mode='Fixed', fixed=90)
        self.assertTrue(wait(lambda: self.ite.regs[FAN_PWM_CTRL_EXT_REG[0]] == round(90*255/100)))
        self.svc.stop()
        self.assertEqual(self.ite.regs, self.original)
    def test_returning_to_auto_restores_that_header(self):
        self.svc.start(); wait(lambda: self.svc.snapshot()['headers'])
        self.svc.configure('8696:3', mode='Fixed', fixed=60)
        self.assertTrue(wait(lambda: self.header(3)['target'] == 60))
        self.svc.configure('8696:3', mode='Auto (BIOS)')
        self.assertTrue(wait(lambda: self.ite.regs == self.original and self.header(3)['target'] is None))
    def test_overheat_failsafe_hands_everything_back(self):
        self.svc.start(); wait(lambda: self.svc.snapshot()['headers'])
        self.svc.configure('8696:0', mode='Quiet')
        self.assertTrue(wait(lambda: self.header(0)['target'] is not None))
        self.ite.set_temps([40, 45, 88, 38, 50, 33])
        self.assertTrue(wait(lambda: self.svc.snapshot()['failsafe']))
        self.assertIn('88', self.svc.snapshot()['failsafe'])
        self.assertEqual(self.ite.regs[FAN_PWM_CTRL_REG[0]], self.original[FAN_PWM_CTRL_REG[0]])
        self.assertEqual(self.header(0)['mode'], 'Auto (BIOS)')
    def test_stalled_fan_is_returned_to_bios(self):
        self.svc.start(); wait(lambda: self.svc.snapshot()['headers'])
        self.svc.configure('8696:1', mode='Fixed', fixed=40)
        self.assertTrue(wait(lambda: self.header(1)['target'] == 40))
        self.ite.set_rpm([1350, 0, 0, 675, 2700, 1500])
        self.assertTrue(wait(lambda: self.header(1)['mode'] == 'Auto (BIOS)'))
        self.assertIn('Stopped spinning', self.header(1)['note'])
        self.assertEqual(self.ite.regs[FAN_PWM_CTRL_REG[1]], self.original[FAN_PWM_CTRL_REG[1]])
    def test_hardware_error_mid_control_restores_and_reports(self):
        self.svc.start(); wait(lambda: self.svc.snapshot()['headers'])
        self.svc.configure('8696:0', mode='Fixed', fixed=70)
        self.assertTrue(wait(lambda: self.header(0)['target'] == 70))
        self.bus.fail_after = self.bus.calls + 5
        self.assertTrue(wait(lambda: not self.svc.running))
        self.assertIn('hardware error', self.svc.snapshot()['status'])
    def test_unavailable_driver_is_reported(self):
        def denied(): raise FanError('Fan control needs administrator rights.')
        svc = FanService(denied); svc.start()
        self.assertTrue(wait(lambda: not svc.running))
        self.assertIn('administrator', svc.snapshot()['status'])


if __name__ == '__main__':
    unittest.main()
