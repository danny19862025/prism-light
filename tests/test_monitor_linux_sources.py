# SPDX-License-Identifier: GPL-3.0-only
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prism_monitor import linux_sources
from prism_monitor.linux_sources import LinuxGpuSource, LinuxSystemSource


def write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class FakeLinux:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sys = Path(self.tmp.name) / 'sys'
        self.proc = Path(self.tmp.name) / 'proc'
        self.set_counters(cpu=(100, 0, 100, 800, 0), net=(1000, 500), disk=(10, 20, 100), energy=1_000_000)
        write(self.proc, 'cpuinfo', 'processor : 0\nmodel name : Test Ryzen 9\n')
        write(self.proc, 'meminfo', 'MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\nSwapTotal: 2000 kB\nSwapFree: 1500 kB\n')
        write(self.sys, 'devices/system/cpu/cpu0/cpufreq/scaling_cur_freq', '3000000')
        write(self.sys, 'devices/system/cpu/cpu1/cpufreq/scaling_cur_freq', '5000000')
        cpu = 'class/hwmon/hwmon0/'
        write(self.sys, cpu + 'name', 'k10temp')
        write(self.sys, cpu + 'temp1_input', '41500')
        write(self.sys, cpu + 'temp1_label', 'Tctl')
        write(self.sys, cpu + 'temp3_input', '35000')
        write(self.sys, cpu + 'temp3_label', 'Tccd1')
        board = 'class/hwmon/hwmon1/'
        write(self.sys, board + 'name', 'nct6799')
        write(self.sys, board + 'fan2_input', '850')
        write(self.sys, 'class/powercap/intel-rapl:0/name', 'package-0')
        write(self.sys, 'class/powercap/intel-rapl:0/max_energy_range_uj', '65532610987')
        (self.sys / 'class/net/eth0/device').mkdir(parents=True)
        (self.sys / 'class/net/lo').mkdir(parents=True)
        (self.sys / 'block/nvme0n1/device').mkdir(parents=True)
        (self.sys / 'block/loop0').mkdir(parents=True)

    def set_counters(self, cpu, net, disk, energy):
        write(self.proc, 'stat', 'cpu  ' + ' '.join(map(str, cpu)) + ' 0 0 0 0 0\ncpu0 1 1 1 1\n')
        write(self.proc, 'net/dev', 'Inter-|\n face |\n    lo: 9999 0 0 0 0 0 0 0 9999 0 0 0 0 0 0 0\n'
              f'  eth0: {net[0]} 0 0 0 0 0 0 0 {net[1]} 0 0 0 0 0 0 0\n')
        r, w, busy = disk
        write(self.proc, 'diskstats', f' 259 0 nvme0n1 1 0 {r} 0 1 0 {w} 0 0 {busy} 0\n'
              ' 7 0 loop0 1 0 99999 0 1 0 99999 0 0 99999 0\n')
        write(self.sys, 'class/powercap/intel-rapl:0/energy_uj', str(energy))

    def close(self):
        self.tmp.cleanup()


@unittest.skipUnless(sys.platform.startswith('linux'), 'fake /sys paths such as intel-rapl:0 are not valid Windows file names')
class LinuxSystemSourceTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLinux()
        self.addCleanup(self.fake.close)

    def test_readings_from_kernel_files(self):
        clock = iter([10.0, 12.0, 12.0])
        with mock.patch.object(linux_sources.time, 'monotonic', lambda: next(clock)):
            source = LinuxSystemSource(self.fake.sys, self.fake.proc)
            self.fake.set_counters(cpu=(200, 0, 200, 1400, 0), net=(5000, 2500), disk=(4106, 2068, 1100),
                                   energy=91_000_000)
            out = source.read()
        self.assertEqual(source.cpu_name, 'Test Ryzen 9')
        self.assertAlmostEqual(out['cpu_util'].value, 25.0)
        self.assertAlmostEqual(out['cpu_clock'].value, 4000.0)
        self.assertAlmostEqual(out['cpu_power'].value, 45.0)
        self.assertAlmostEqual(out['cpu_temp'].value, 41.5)
        self.assertEqual(source.info['cpu_temp_sensor'], 'Tctl')
        self.assertEqual(out['ram_used'].value, 600 * 1024)
        self.assertEqual(out['ram_used'].extra, 1000 * 1024)
        self.assertEqual((out['swap_used'].value, out['swap_used'].extra), (500 * 1024, 2000 * 1024))
        self.assertAlmostEqual(out['net_down'].value, 2000.0)   # loopback excluded
        self.assertAlmostEqual(out['net_up'].value, 1000.0)
        self.assertAlmostEqual(out['disk_read'].value, 4096 * 512 / 2)   # loop device excluded
        self.assertAlmostEqual(out['disk_active'].value, 50.0)
        fans = source.fans()
        self.assertEqual([label for label, _ in fans.values()], ['Fan 2'])

    def test_unreadable_rapl_leaves_power_out(self):
        (self.fake.sys / 'class/powercap/intel-rapl:0/energy_uj').unlink()
        source = LinuxSystemSource(self.fake.sys, self.fake.proc)
        self.assertNotIn('cpu_power', source.read())


@unittest.skipUnless(sys.platform.startswith('linux'), 'fake /sys tree with symlinks, which need admin rights on Windows')
class LinuxGpuSourceTest(unittest.TestCase):
    def test_picks_discrete_amd_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            sys_root = Path(tmp)
            for index, (vram, edge) in enumerate(((512 << 20, 38000), (16 << 30, 42000))):
                dev = sys_root / f'devices/pci/000{index}'
                write(dev, 'mem_info_vram_total', str(vram))
                write(dev, 'mem_info_vram_used', str(vram // 8))
                write(dev, 'gpu_busy_percent', '7')
                chip = sys_root / f'class/hwmon/hwmon{index}'
                write(chip, 'name', 'amdgpu')
                write(chip, 'temp1_input', str(edge))
                write(chip, 'temp1_label', 'edge')
                write(chip, 'freq1_input', '2500000000')
                write(chip, 'freq1_label', 'sclk')
                write(chip, 'power1_average', '210000000')
                (chip / 'device').symlink_to(dev)
            with mock.patch.object(linux_sources, '_pci_name', return_value='AMD Test GPU'):
                gpu = LinuxGpuSource(sys_root)
            out = gpu.read()
        self.assertEqual(gpu.device, 'AMD Test GPU')
        self.assertAlmostEqual(out['gpu_temp'].value, 42.0)
        self.assertAlmostEqual(out['gpu_clock'].value, 2500.0)
        self.assertAlmostEqual(out['gpu_power'].value, 210.0)
        self.assertEqual(out['gpu_util'].value, 7)
        self.assertEqual(out['gpu_vram'].extra, 16 << 30)

    def test_no_amd_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OSError):
                LinuxGpuSource(Path(tmp))


if __name__ == '__main__':
    unittest.main()
