from pathlib import Path
import re
import subprocess
import sys


def available_mb():
    try:
        if sys.platform.startswith("linux"):
            match = re.search(r"^MemAvailable:\s+(\d+)\s+kB", Path("/proc/meminfo").read_text(), re.M)
            return int(match[1]) // 1024 if match else None
        if sys.platform == "darwin":
            text = subprocess.check_output(["vm_stat"], text=True, timeout=10)
            page_size = int(re.search(r"page size of (\d+) bytes", text)[1])
            pages = sum(int(re.search(rf"Pages {name}:\s+(\d+)", text)[1])
                        for name in ("free", "inactive", "speculative", "purgeable"))
            return pages * page_size // (1024 * 1024)
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        pass
    return None


def can_start(cfg, running):
    avail = available_mb()
    return running < cfg.max_concurrent and (avail is None or avail - cfg.chrome_peak_mb >= cfg.mem_reserve_mb)
