"""Docker container status and statistics collection."""

import logging
from typing import Optional, List, Dict

try:
    import docker
except ImportError:
    docker = None

logger = logging.getLogger(__name__)


class DockerStats:
    """Collect Docker container status and metrics."""

    _client = None

    @classmethod
    def get_client(cls):
        """Get or create Docker client."""
        if docker is None:
            return None

        if cls._client is None:
            try:
                cls._client = docker.from_env()
            except Exception as e:
                logger.error(f"Failed to connect to Docker: {e}")
                return None

        return cls._client

    @classmethod
    def get_container_status(cls, container_name: str) -> Optional[dict]:
        """Get status of a specific container."""
        client = cls.get_client()
        if client is None:
            return None

        try:
            container = client.containers.get(container_name)

            return {
                "name": container.name,
                "id": container.id[:12],
                "status": container.status,
                "state": container.attrs.get("State", {}),
                "created": container.attrs.get("Created"),
                "image": container.image.short_id,
            }
        except Exception as e:
            logger.error(f"Error getting container status for {container_name}: {e}")
            return None

    @classmethod
    def get_container_stats(cls, container_name: str) -> Optional[dict]:
        """Get CPU and memory stats for a container."""
        client = cls.get_client()
        if client is None:
            return None

        try:
            container = client.containers.get(container_name)

            # Get current stats (non-blocking)
            stats = container.stats(stream=False)

            # Calculate CPU percentage
            cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            system_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
            cpu_percent = (cpu_delta / system_delta) * 100.0 if system_delta > 0 else 0

            # Calculate memory percentage
            memory_used = stats["memory_stats"]["usage"]
            memory_limit = stats["memory_stats"]["limit"]
            memory_percent = (memory_used / memory_limit) * 100.0 if memory_limit > 0 else 0

            # Get uptime
            started_at = container.attrs["State"]["StartedAt"]
            uptime_seconds = None
            if started_at:
                from datetime import datetime
                from dateutil import parser as date_parser

                started = date_parser.parse(started_at)
                uptime_seconds = int((datetime.utcnow() - started.replace(tzinfo=None)).total_seconds())

            return {
                "name": container.name,
                "status": container.status,
                "cpu_percent": round(cpu_percent, 2),
                "memory_percent": round(memory_percent, 2),
                "memory_usage": memory_used,
                "memory_limit": memory_limit,
                "uptime_seconds": uptime_seconds,
            }
        except Exception as e:
            logger.error(f"Error getting container stats for {container_name}: {e}")
            return None

    @classmethod
    def get_all_containers_status(cls) -> Optional[List[dict]]:
        """Get status of all running containers."""
        client = cls.get_client()
        if client is None:
            return None

        try:
            containers = client.containers.list()
            status_list = []

            for container in containers:
                status = {
                    "name": container.name,
                    "id": container.id[:12],
                    "status": container.status,
                    "image": container.image.short_id,
                }

                # Try to get stats
                try:
                    stats = cls.get_container_stats(container.name)
                    if stats:
                        status.update({
                            "cpu_percent": stats["cpu_percent"],
                            "memory_percent": stats["memory_percent"],
                            "uptime_seconds": stats["uptime_seconds"],
                        })
                except Exception:
                    pass

                status_list.append(status)

            return status_list
        except Exception as e:
            logger.error(f"Error getting containers list: {e}")
            return None

    @classmethod
    def get_service_health(cls) -> dict:
        """
        Get health status of key services.
        Returns: {service_name: "online"|"offline"|"unknown"}
        """
        expected_containers = [
            "cat_postgres",
            "cat_redis",
            "cat_backend",
            "cat_celery_worker",
            "cat_celery_beat",
        ]

        health = {}

        for container_name in expected_containers:
            status = cls.get_container_status(container_name)
            if status:
                health[container_name] = "online" if status["status"] == "running" else "offline"
            else:
                health[container_name] = "offline"

        return health

    @classmethod
    def get_all_stats(cls) -> dict:
        """Get all Docker stats and status information."""
        return {
            "containers": cls.get_all_containers_status(),
            "service_health": cls.get_service_health(),
        }
