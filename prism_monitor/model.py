# SPDX-License-Identifier: GPL-3.0-only
"""Metric catalogue and reading model.

A reading is either a real measurement with the time it was taken, or absent.
Absent and stale readings are displayed as unavailable, never as zero.
"""
import math
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Reading:
    value: float
    ts: float                      # time.monotonic() when measured
    source: str = ''
    extra: float = None            # e.g. total for used/total metrics; battery: 1 charging, 2 full


def reading(value, source, extra=None, now=None):
    """A Reading for a finite number, else None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return Reading(value, time.monotonic() if now is None else now, source, extra)


@dataclass(frozen=True)
class Metric:
    key: str
    group: str
    label: str
    unit: str
    primary: bool = False          # shown large with a history graph
    graph_max: float = None        # fixed graph scale (None = auto)
    default: bool = True


# Groups in display order: (id, title)
GROUPS = (('cpu', 'CPU'), ('gpu', 'GPU'), ('ram', 'Memory'), ('fps', 'Game'), ('net', 'Network'),
          ('disk', 'Disk'), ('fans', 'Fans'), ('battery', 'Batteries'), ('clock', 'Time'))

METRICS = (
    Metric('cpu_temp', 'cpu', 'Temp', '°C', True, 100),
    Metric('cpu_util', 'cpu', 'Load', '%', True, 100),
    Metric('cpu_clock', 'cpu', 'Clock', 'GHz'),
    Metric('cpu_power', 'cpu', 'Power', 'W'),
    Metric('gpu_temp', 'gpu', 'Temp', '°C', True, 100),
    Metric('gpu_util', 'gpu', 'Load', '%', True, 100),
    Metric('gpu_hotspot', 'gpu', 'Hotspot', '°C'),
    Metric('gpu_clock', 'gpu', 'Clock', 'MHz'),
    Metric('gpu_power', 'gpu', 'Power', 'W'),
    Metric('gpu_vram', 'gpu', 'VRAM', 'GB'),
    Metric('gpu_fan', 'gpu', 'Fan', 'rpm', default=False),
    Metric('ram_used', 'ram', 'Used', 'GB', True, 100),
    Metric('fps', 'fps', 'FPS', 'fps', True),
    Metric('frametime', 'fps', 'Frame time', 'ms'),
    Metric('net_down', 'net', 'Down', 'B/s', True, default=False),
    Metric('net_up', 'net', 'Up', 'B/s', True, default=False),
    Metric('disk_active', 'disk', 'Active', '%', True, 100, default=False),
    Metric('disk_read', 'disk', 'Read', 'B/s', default=False),
    Metric('disk_write', 'disk', 'Write', 'B/s', default=False),
    Metric('fans', 'fans', 'Fans', 'rpm', default=False),
    Metric('battery', 'battery', 'Devices', '%'),
    Metric('clock', 'clock', 'Clock', '', default=False),
)
BY_KEY = {m.key: m for m in METRICS}
DEFAULT_VISIBLE = tuple(m.key for m in METRICS if m.default)


def is_fresh(item, now, stale_after):
    return item is not None and now - item.ts <= stale_after


def fmt_rate(value):
    """Bytes per second → compact text."""
    for unit, size in (('GB/s', 1 << 30), ('MB/s', 1 << 20), ('KB/s', 1 << 10)):
        if value >= size:
            v = value / size
            return f'{v:.1f} {unit}' if v < 100 else f'{v:.0f} {unit}'
    return f'{value:.0f} B/s'


def format_value(key, item):
    """Display text for a fresh reading (callers handle missing/stale)."""
    v = item.value
    if key in ('cpu_temp', 'gpu_temp', 'gpu_hotspot'):
        return f'{v:.0f}°'
    if key in ('cpu_util', 'gpu_util', 'disk_active'):
        return f'{v:.0f}%'
    if key == 'cpu_clock':
        return f'{v / 1000:.2f} GHz'
    if key == 'gpu_clock':
        return f'{v:.0f} MHz'
    if key in ('cpu_power', 'gpu_power'):
        return f'{v:.0f} W'
    if key in ('gpu_vram', 'ram_used'):
        gib = 1 << 30
        if item.extra:
            return f'{v / gib:.1f}/{item.extra / gib:.0f} GB'
        return f'{v / gib:.1f} GB'
    if key == 'fps':
        return f'{v:.0f}'
    if key == 'frametime':
        return f'{v:.1f} ms'
    if key in ('net_down', 'net_up', 'disk_read', 'disk_write'):
        return fmt_rate(v)
    if key.startswith('bat:'):
        return f'{v:.0f}%'
    if key in ('gpu_fan',) or key.startswith('fan:'):
        return f'{v:.0f} rpm'
    return f'{v:g}'


@dataclass
class Snapshot:
    readings: dict = field(default_factory=dict)   # key -> Reading
    fans: dict = field(default_factory=dict)       # fan id -> (label, Reading)
    batteries: dict = field(default_factory=dict)  # device id -> (name, Reading, coarse level text)
    info: dict = field(default_factory=dict)       # device names, fps app name, etc.
    status: dict = field(default_factory=dict)     # source -> human-readable status
