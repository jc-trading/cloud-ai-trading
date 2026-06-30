# System Monitoring Fix Report

Date: 2026-04-13

## Scope

Reviewed and fixed backend issues in the AI-generated system monitoring module.
The fixes are limited to backend monitoring routes, services, Celery task wiring,
Docker Compose monitoring support, migrations, schemas, and accidental generated
files in the backend folder.

## Issues Fixed

1. Fixed broken FastAPI auth import in `backend/app/modules/system/routes.py`.
   The module now imports RBAC dependencies from `app.dependencies`.

2. Fixed broken Celery app import in `backend/app/modules/system/celery_health.py`.
   The helper now lazily imports `tasks.celery_app` to avoid wrong package paths
   and circular imports during task loading.

3. Corrected system monitoring API path.
   The router prefix changed from `/api/system` to `/system`, so with the global
   API v1 prefix the final route is `/api/v1/system/...` instead of
   `/api/v1/api/system/...`.

4. Added system permission enforcement.
   System monitoring HTTP routes now require the `manage_system` permission
   instead of allowing every authenticated user.

5. Added WebSocket permission enforcement.
   The log-stream WebSocket now accepts an access token query parameter and
   validates that the user has `manage_system`.

6. Registered the new Celery monitoring tasks explicitly.
   `tasks.system_tasks` is included in the Celery app import list so workers can
   receive scheduled monitoring jobs.

7. Added the monitoring jobs to Celery health expectations.
   `collect_system_metrics`, `sync_task_statuses`, `cleanup_old_logs`, and
   `cleanup_old_metrics` are now tracked by the task health sync.

8. Fixed false Celery Beat offline checks.
   Task sync now checks the configured Beat schedule instead of using worker
   `inspect().scheduled()`, which only reports ETA/countdown tasks and can be
   empty even when periodic tasks are configured.

9. Reduced default monitoring write frequency.
   System metrics now default to every 60 seconds, and task health sync defaults
   to every 5 minutes.

10. Stopped read endpoints from writing metric history rows.
   GET `/metrics` and the log-stream WebSocket now collect live metrics with
   `save=False`; only scheduled collection persists historical metrics.

11. Moved blocking metrics collectors off the FastAPI event loop where practical.
   System, Docker, and Celery status collection now run through `asyncio.to_thread`
   when called by the async service.

12. Switched log counting to SQL `COUNT(*)`.
    The log list endpoint no longer loads all matching rows just to calculate
    pagination totals.

13. Switched retention cleanup to bulk SQL deletes.
    Old system logs and metrics are deleted with database-level delete statements
    instead of loading and deleting rows one by one.

14. Connected Docker SDK monitoring inside Compose.
    The backend and Celery worker containers now mount `/var/run/docker.sock`
    read-only so Docker container health can be read from inside the containers.

15. Fixed Pydantic response schema UUID handling.
    System monitoring response schemas now expose UUID fields as `UUID` instead
    of `str`, matching the SQLAlchemy models.

16. Updated migration defaults.
    Migration `007_system_monitoring_tables.py` now uses database-side defaults
    for UUID primary keys and task status counters.

17. Removed accidental empty backend files.
    Deleted `backend/=6.0.0` and `backend/=7.0.0`.

## Remaining Notes

- The current environment does not have backend dependencies installed, so full
  application import and API tests could not be run locally without installing
  packages.
- Docker socket access is required for the existing Docker SDK based monitoring
  approach. It is mounted read-only, but it still exposes host Docker metadata to
  the backend and worker containers.
- The WebSocket log stream now expects an access token query parameter. The
  frontend should connect to `/api/v1/system/ws/logs?token=<access_token>`.

## Files Changed

- `backend/app/config.py`
- `backend/app/modules/system/celery_health.py`
- `backend/app/modules/system/routes.py`
- `backend/app/modules/system/schemas.py`
- `backend/app/modules/system/service.py`
- `backend/migrations/versions/007_system_monitoring_tables.py`
- `backend/tasks/celery_app.py`
- `backend/tasks/system_tasks.py`
- `docker-compose.yml`
- `SYSTEM_MONITORING_FIX_REPORT.md`
