"""Pi RAM + SD (root disk) snapshot for the idle OLED."""

from __future__ import annotations

import os
from typing import Optional


def format_bytes(n: int) -> str:
    n = max(0, int(n))
    if n >= 1024**3:
        g = n / 1024**3
        return f"{g:.1f}G" if g < 10 else f"{g:.0f}G"
    if n >= 1024**2:
        m = n / 1024**2
        return f"{m:.0f}M" if m >= 10 else f"{m:.1f}M"
    return f"{n / 1024:.0f}K"


def _pct(used: int, total: int) -> int:
    if total <= 0:
        return 0
    return min(100, int(round(100.0 * used / total)))


def read_ram() -> Optional[tuple[int, int]]:
    """Return (used_bytes, total_bytes) from /proc/meminfo, or None off-Pi."""
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                info[parts[0].rstrip(":")] = int(parts[1]) * 1024
        total = info.get("MemTotal")
        if not total:
            return None
        available = info.get("MemAvailable", info.get("MemFree", 0))
        used = max(0, total - available)
        return used, total
    except (OSError, ValueError):
        return None


def read_disk(path: str = "/") -> Optional[tuple[int, int]]:
    """Return (used_bytes, total_bytes) for the root filesystem."""
    try:
        st = os.statvfs(path)
    except (OSError, ValueError, AttributeError):
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = max(0, total - free)
    if total <= 0:
        return None
    return used, total


def memory_lines() -> tuple[str, str]:
    ram = read_ram()
    disk = read_disk("/")
    if ram:
        used, total = ram
        ram_line = f"RAM {format_bytes(used)}/{format_bytes(total)} {_pct(used, total)}%"
    else:
        ram_line = "RAM --"
    if disk:
        used, total = disk
        disk_line = f"SD  {format_bytes(used)}/{format_bytes(total)} {_pct(used, total)}%"
    else:
        disk_line = "SD  --"
    return ram_line, disk_line
