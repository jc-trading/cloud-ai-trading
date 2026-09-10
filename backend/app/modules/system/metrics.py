"""Host gauges kept after the Phase B monitoring downgrade.

CPU/memory/disk/network collection (psutil) and the container stats (docker)
went with the metrics tables — nothing read them. What remains is the bar-store
size gauge from the market-data work.
"""

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BAR_STORE_TTL_SECONDS = 3600
_bar_store_cache: dict = {}


class SystemMetrics:
    """Collect host-level gauges."""

    @staticmethod
    def get_bar_store_metrics(path: Optional[str] = None) -> Optional[dict]:
        """Size of the Parquet bar store (stock-market-data/). It is the one
        directory on this host that grows without bound — 100 symbols x 5 years
        of 1min is ~126k files — so its share of the disk is worth a gauge.

        Walking that tree costs seconds, so a measurement is reused for
        BAR_STORE_TTL_SECONDS; the number moves once a night anyway."""
        try:
            from quant import config as quant_config

            root = Path(path) if path else quant_config.BARS_ROOT
        except Exception as e:
            logger.error(f"Error resolving bar store path: {e}")
            return None

        key = str(root)
        cached = _bar_store_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < BAR_STORE_TTL_SECONDS:
            return cached[1]

        if not root.exists():
            result = {"path": key, "bytes": 0, "files": 0}
            _bar_store_cache[key] = (time.monotonic(), result)
            return result
        total = 0
        files = 0
        try:
            for entry in root.rglob("*"):
                if entry.is_file():
                    total += entry.stat().st_size
                    files += 1
        except OSError as e:
            logger.error(f"Error sizing bar store: {e}")
            return None
        result = {"path": key, "bytes": total, "files": files}
        _bar_store_cache[key] = (time.monotonic(), result)
        return result

    @staticmethod
    def format_bytes(bytes_value: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_value < 1024.0:
                return f"{bytes_value:.1f}{unit}"
            bytes_value /= 1024.0
        return f"{bytes_value:.1f}PB"
