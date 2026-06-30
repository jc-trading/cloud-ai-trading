"""System metrics collection using psutil."""

import logging
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger(__name__)


class SystemMetrics:
    """Collect system-level metrics (CPU, memory, disk)."""

    @staticmethod
    def get_cpu_metrics() -> Optional[dict]:
        """Get CPU usage metrics."""
        if psutil is None:
            return None

        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            cpu_count = psutil.cpu_count()
            load_avg = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (None, None, None)

            return {
                "percent": float(cpu_percent),
                "cores": cpu_count,
                "load_average": {
                    "1min": float(load_avg[0]) if load_avg[0] is not None else None,
                    "5min": float(load_avg[1]) if load_avg[1] is not None else None,
                    "15min": float(load_avg[2]) if load_avg[2] is not None else None,
                },
            }
        except Exception as e:
            logger.error(f"Error getting CPU metrics: {e}")
            return None

    @staticmethod
    def get_memory_metrics() -> Optional[dict]:
        """Get memory usage metrics."""
        if psutil is None:
            return None

        try:
            memory = psutil.virtual_memory()

            return {
                "total": memory.total,  # bytes
                "used": memory.used,
                "available": memory.available,
                "percent": float(memory.percent),
                "free": memory.free,
            }
        except Exception as e:
            logger.error(f"Error getting memory metrics: {e}")
            return None

    @staticmethod
    def get_disk_metrics(path: str = "/") -> Optional[dict]:
        """Get disk usage metrics."""
        if psutil is None:
            return None

        try:
            disk = psutil.disk_usage(path)

            return {
                "total": disk.total,  # bytes
                "used": disk.used,
                "free": disk.free,
                "percent": float(disk.percent),
            }
        except Exception as e:
            logger.error(f"Error getting disk metrics: {e}")
            return None

    @staticmethod
    def get_network_metrics() -> Optional[dict]:
        """Get network I/O metrics."""
        if psutil is None:
            return None

        try:
            net_io = psutil.net_io_counters()

            return {
                "bytes_sent": net_io.bytes_sent,
                "bytes_recv": net_io.bytes_recv,
                "packets_sent": net_io.packets_sent,
                "packets_recv": net_io.packets_recv,
                "errin": net_io.errin,
                "errout": net_io.errout,
                "dropin": net_io.dropin,
                "dropout": net_io.dropout,
            }
        except Exception as e:
            logger.error(f"Error getting network metrics: {e}")
            return None

    @staticmethod
    def get_all_metrics() -> dict:
        """Get all system metrics in one call."""
        return {
            "cpu": SystemMetrics.get_cpu_metrics(),
            "memory": SystemMetrics.get_memory_metrics(),
            "disk": SystemMetrics.get_disk_metrics(),
            "network": SystemMetrics.get_network_metrics(),
        }

    @staticmethod
    def format_bytes(bytes_value: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_value < 1024.0:
                return f"{bytes_value:.1f}{unit}"
            bytes_value /= 1024.0
        return f"{bytes_value:.1f}PB"

    @staticmethod
    def is_high_usage(metrics: dict, cpu_threshold: float = 80.0, memory_threshold: float = 85.0) -> dict:
        """Check if system is under high load."""
        alerts = {}

        cpu = metrics.get("cpu")
        if cpu and cpu["percent"] > cpu_threshold:
            alerts["cpu_high"] = {
                "threshold": cpu_threshold,
                "current": cpu["percent"],
                "message": f"High CPU usage: {cpu['percent']:.1f}%",
            }

        memory = metrics.get("memory")
        if memory and memory["percent"] > memory_threshold:
            alerts["memory_high"] = {
                "threshold": memory_threshold,
                "current": memory["percent"],
                "message": f"High memory usage: {memory['percent']:.1f}%",
            }

        return alerts
