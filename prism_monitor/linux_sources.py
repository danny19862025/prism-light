# SPDX-License-Identifier: GPL-3.0-only
"""Linux metric sources: the kernel's own counters, no root and no helper.

* LinuxSystemSource: /proc/stat (CPU load), cpufreq (CPU clock), hwmon
  k10temp/zenpower/coretemp (CPU temperature), powercap RAPL (CPU package
  power), /proc/meminfo (RAM), /proc/net/dev (network), /proc/diskstats
  (disk), and motherboard fan headers exposed through hwmon.
* LinuxGpuSource: the amdgpu driver's hwmon and sysfs files (temperatures,
  power, clock, fan, load, VRAM) for the discrete card.

Every reading that cannot be read is left out, never shown as zero.
"""
import re
import subprocess
import sys
import time
from pathlib import Path

from .model import reading

SYS = Path('/sys')
PROC = Path('/proc')
SRC = 'linux'

# CPU temperature drivers and their labels, best first.
CPU_TEMP_CHIPS = ('k10temp', 'zenpower', 'coretemp', 'cpu_thermal')
CPU_TEMP_PREFERRED = ('Tctl', 'Tdie', 'Package id 0', 'Tccd1')
# hwmon chips whose fans belong to something other than the motherboard.
NON_BOARD_FAN_CHIPS = ('amdgpu', 'nouveau', 'nvidia', 'i915', 'xe', 'nvme')


def _read(path, default=None):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return default


def _num(path, scale=1.0):
    text = _read(path)
    if text is None:
        return None
    try:
        return float(text) * scale
    except ValueError:
        return None


def hwmon_chips(root=SYS):
    """[(name, path)] for every hwmon device."""
    base = Path(root) / 'class' / 'hwmon'
    try:
        entries = sorted(base.iterdir(), key=lambda p: int(re.sub(r'\D', '', p.name) or 0))
    except OSError:
        return []
    return [(_read(p / 'name', ''), p) for p in entries]


def labelled(chip, kind):
    """{label: input path} for one sensor kind (temp, fan, power, freq) on a hwmon chip."""
    out = {}
    for path in sorted(chip.glob(f'{kind}*_input')):
        index = path.name[len(kind):-len('_input')]
        label = _read(chip / f'{kind}{index}_label') or f'{kind}{index}'
        out.setdefault(label, path)
    return out


def cpu_model(proc=PROC):
    text = _read(Path(proc) / 'cpuinfo', '')
    match = re.search(r'^model name\s*:\s*(.+)$', text, re.M)
    return match.group(1).strip() if match else 'CPU'


class _Rate:
    """Per-second rate between two cumulative counter samples."""

    def __init__(self):
        self.last = {}

    def __call__(self, key, value, now, wrap=None):
        previous = self.last.get(key)
        self.last[key] = (value, now)
        if previous is None or now <= previous[1]:
            return None
        delta = value - previous[0]
        if delta < 0:
            if not wrap:
                return None
            delta += wrap
        return delta / (now - previous[1])


class LinuxSystemSource:
    name = 'system'
    VIRTUAL_NICS = ('lo', 'veth', 'docker', 'br-', 'virbr', 'tun', 'tap', 'wg', 'tailscale', 'zt')

    def __init__(self, sys_root=SYS, proc_root=PROC):
        self.sys = Path(sys_root)
        self.proc = Path(proc_root)
        if not (self.proc / 'stat').exists():
            raise OSError('/proc/stat is not readable.')
        self.cpu_name = cpu_model(self.proc)
        self.base_mhz = None
        self.rate = _Rate()
        self.cpu_last = None
        self.info = {}
        self.temp_chip = self._find_cpu_temp_chip()
        self.rapl = self._find_rapl()
        self.read()   # rate counters need a first sample

    # -- discovery -----------------------------------------------------
    def _find_cpu_temp_chip(self):
        chips = {name: path for name, path in hwmon_chips(self.sys)}
        for name in CPU_TEMP_CHIPS:
            if name in chips:
                return chips[name]
        return None

    def _find_rapl(self):
        """(energy file, wrap range) for the CPU package, or None when unreadable."""
        base = self.sys / 'class' / 'powercap'
        try:
            zones = sorted(base.glob('*-rapl:*'))
        except OSError:
            return None
        for zone in zones:
            if zone.name.count(':') != 1 or not (_read(zone / 'name', '').startswith('package')):
                continue
            energy = zone / 'energy_uj'
            if _num(energy) is None:
                continue   # root-only on this kernel
            return energy, _num(zone / 'max_energy_range_uj')
        return None

    def physical_nics(self):
        base = self.sys / 'class' / 'net'
        try:
            names = [p.name for p in base.iterdir()]
        except OSError:
            return set()
        return {n for n in names if (base / n / 'device').exists() and not n.startswith(self.VIRTUAL_NICS)}

    def physical_disks(self):
        base = self.sys / 'block'
        try:
            names = [p.name for p in base.iterdir()]
        except OSError:
            return set()
        return {n for n in names if (base / n / 'device').exists() and not n.startswith(('loop', 'ram', 'zram', 'dm-', 'sr'))}

    # -- reading -------------------------------------------------------
    def read(self):
        now = time.monotonic()
        out = {}
        self._cpu(out, now)
        self._temps(out, now)
        self._memory(out, now)
        self._network(out, now)
        self._disk(out, now)
        return {k: v for k, v in out.items() if v is not None}

    def _cpu(self, out, now):
        line = (_read(self.proc / 'stat', '') or '').splitlines()
        if line and line[0].startswith('cpu '):
            fields = [int(x) for x in line[0].split()[1:]]
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
            total = sum(fields[:8])
            if self.cpu_last:
                d_total, d_idle = total - self.cpu_last[0], idle - self.cpu_last[1]
                if d_total > 0:
                    out['cpu_util'] = reading(min(100.0, max(0.0, 100.0 * (d_total - d_idle) / d_total)), SRC, now=now)
            self.cpu_last = (total, idle)
        freqs = [f for f in (_num(p, 1 / 1000) for p in self.sys.glob('devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq'))
                 if f and f > 100]
        if freqs:
            out['cpu_clock'] = reading(sum(freqs) / len(freqs), SRC, now=now)
        if self.rapl:
            energy, wrap = self.rapl
            joules = _num(energy, 1e-6)
            if joules is not None:
                watts = self.rate('rapl', joules, now, wrap=wrap * 1e-6 if wrap else None)
                if watts is not None and 0 < watts < 1000:
                    out['cpu_power'] = reading(watts, SRC, now=now)

    def cpu_temps(self):
        if self.temp_chip is None:
            return {}
        temps = {}
        for label, path in labelled(self.temp_chip, 'temp').items():
            value = _num(path, 1 / 1000)
            if value is not None and 0 < value < 125:
                temps[label] = value
        return temps

    def _temps(self, out, now):
        temps = self.cpu_temps()
        self.info = {}
        if temps:
            chosen = next((k for k in CPU_TEMP_PREFERRED if k in temps), next(iter(temps)))
            out['cpu_temp'] = reading(temps[chosen], SRC, now=now)
            self.info = {'cpu_temps': temps, 'cpu_temp_sensor': chosen}

    def _memory(self, out, now):
        values = {}
        for line in (_read(self.proc / 'meminfo', '') or '').splitlines():
            key, _, rest = line.partition(':')
            parts = rest.split()
            if parts:
                values[key] = int(parts[0]) * 1024
        total, available = values.get('MemTotal'), values.get('MemAvailable')
        if total and available is not None:
            out['ram_used'] = reading(total - available, SRC, extra=total, now=now)

    def _network(self, out, now):
        nics = self.physical_nics()
        down = up = 0
        found = False
        for line in (_read(self.proc / 'net' / 'dev', '') or '').splitlines()[2:]:
            name, _, rest = line.partition(':')
            if name.strip() not in nics:
                continue
            fields = rest.split()
            down += int(fields[0])
            up += int(fields[8])
            found = True
        if found:
            out['net_down'] = reading(self.rate('net_down', down, now), SRC, now=now)
            out['net_up'] = reading(self.rate('net_up', up, now), SRC, now=now)

    def _disk(self, out, now):
        disks = self.physical_disks()
        read = write = busy = 0
        count = 0
        for line in (_read(self.proc / 'diskstats', '') or '').splitlines():
            fields = line.split()
            if len(fields) < 14 or fields[2] not in disks:
                continue
            read += int(fields[5]) * 512
            write += int(fields[9]) * 512
            busy = max(busy, int(fields[12]))   # busiest disk, like Windows' _Total idle time
            count += 1
        if count:
            out['disk_read'] = reading(self.rate('disk_read', read, now), SRC, now=now)
            out['disk_write'] = reading(self.rate('disk_write', write, now), SRC, now=now)
            active = self.rate('disk_busy', busy / 1000, now)
            if active is not None:
                out['disk_active'] = reading(min(100.0, max(0.0, active * 100)), SRC, now=now)

    def fans(self, now=None):
        """{fan id: (label, Reading)} for motherboard fan headers the kernel exposes."""
        now = time.monotonic() if now is None else now
        fans = {}
        for chip_name, chip in hwmon_chips(self.sys):
            if chip_name in NON_BOARD_FAN_CHIPS:
                continue
            for label, path in labelled(chip, 'fan').items():
                rpm = _num(path)
                if rpm is not None and 0 <= rpm < 20000:
                    fans[f'{chip_name}/{path.name}'] = (label.replace('fan', 'Fan '), reading(rpm, SRC, now=now))
        return fans

    def close(self):
        pass


def _pci_name(address):
    try:
        text = subprocess.run(['lspci', '-mm', '-s', address], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    fields = re.findall(r'"([^"]*)"', text)
    if len(fields) >= 3:
        name = fields[2]
        bracket = re.search(r'\[([^\]]+)\]', name)
        return f"AMD {bracket.group(1) if bracket else name}"
    return None


class LinuxGpuSource:
    """AMD Radeon sensors from the amdgpu kernel driver (no root needed)."""
    name = 'adl'

    def __init__(self, sys_root=SYS):
        self.sys = Path(sys_root)
        cards = []
        for chip_name, chip in hwmon_chips(self.sys):
            if chip_name != 'amdgpu':
                continue
            device = (chip / 'device').resolve()
            vram = _num(device / 'mem_info_vram_total') or 0
            cards.append((vram, chip, device))
        if not cards:
            raise OSError('No AMD GPU with amdgpu sensors found.')
        # The discrete card has far more dedicated VRAM than the iGPU's carve-out.
        self.vram_total, self.chip, self.dev = max(cards, key=lambda c: c[0])
        self.name_ = _pci_name(self.dev.name) or 'AMD GPU'

    @property
    def device(self):
        return self.name_

    def read(self):
        now = time.monotonic()
        out = {}
        temps = labelled(self.chip, 'temp')
        for key, label in (('gpu_temp', 'edge'), ('gpu_hotspot', 'junction')):
            if label in temps:
                value = _num(temps[label], 1 / 1000)
                if value and value > 0:
                    out[key] = reading(value, SRC, now=now)
        power = _num(self.chip / 'power1_average', 1e-6)
        if power is None:
            power = _num(self.chip / 'power1_input', 1e-6)
        if power is not None:
            out['gpu_power'] = reading(power, SRC, now=now)
        clocks = labelled(self.chip, 'freq')
        if 'sclk' in clocks:
            out['gpu_clock'] = reading(_num(clocks['sclk'], 1e-6), SRC, now=now)
        fan = _num(self.chip / 'fan1_input')
        if fan is not None:
            out['gpu_fan'] = reading(fan, SRC, now=now)
        busy = _num(self.dev / 'gpu_busy_percent')
        if busy is not None:
            out['gpu_util'] = reading(busy, SRC, now=now)
        used = _num(self.dev / 'mem_info_vram_used')
        if used is not None:
            out['gpu_vram'] = reading(used, SRC, extra=self.vram_total or None, now=now)
        return {k: v for k, v in out.items() if v is not None}

    def close(self):
        pass


LINUX = sys.platform.startswith('linux')
