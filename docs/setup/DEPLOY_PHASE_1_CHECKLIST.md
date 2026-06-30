# Phase 1 System Monitoring Backend - Deploy Checklist

**Date:** 2026-04-13  
**Component:** System Monitoring Dashboard (Backend)  
**Migration:** 007_system_monitoring_tables.py  
**Dependencies Added:** psutil>=6.0.0, docker>=7.0.0

## ✅ Pre-Deploy Verification

### Code Quality
- [x] All Python files syntax validated
- [x] All imports resolve correctly
- [x] No circular dependencies
- [x] Database models created
- [x] Pydantic schemas defined
- [x] API routes registered
- [x] Celery tasks configured

### Database
- [x] Migration file created (007_system_monitoring_tables.py)
- [x] All tables defined with proper indexes
- [x] Foreign keys configured
- [x] Upgrade/downgrade functions working
- [ ] Migration applied to development database
- [ ] New tables visible in database

### Dependencies
- [x] psutil>=6.0.0 added to requirements.txt
- [x] docker>=7.0.0 added to requirements.txt
- [ ] Dependencies installed in environment
- [ ] No version conflicts

### API & Routes
- [x] 7 REST endpoints defined
- [x] 1 WebSocket endpoint defined
- [x] JWT authentication required on all endpoints
- [x] All routes registered in main.py
- [x] API_V1_PREFIX applied correctly

### Celery Integration
- [x] 4 system monitoring tasks defined
- [x] Beat schedule configured in celery_app.py
- [x] Task auto-discovery enabled
- [x] Async database sessions configured

### Configuration
- [x] System monitoring settings added to config.py
- [x] Defaults set for thresholds and intervals
- [x] Retention policies configured (30 days)

## 🚀 Deployment Steps

### Step 1: Environment Setup
- [ ] Navigate to backend directory: `cd /sessions/gifted-vibrant-wright/mnt/CloudAiTrading/backend`
- [ ] Verify Python environment active
- [ ] Check current requirements.txt version

### Step 2: Install Dependencies
- [ ] Install new dependencies: `pip install psutil>=6.0.0 docker>=7.0.0`
- [ ] Verify installations: `pip show psutil docker`
- [ ] Check for conflicts: `pip check`

### Step 3: Database Migration
- [ ] Review migration file: `cat migrations/versions/007_system_monitoring_tables.py`
- [ ] Run migration: `alembic upgrade head`
- [ ] Verify migration completed successfully
- [ ] Confirm tables created in database
- [ ] Check indexes created correctly

### Step 4: Service Startup Verification
- [ ] Start FastAPI backend: `uvicorn app.main:app --reload`
- [ ] Verify no startup errors
- [ ] Check all routes registered (GET /api/docs)
- [ ] Confirm system module imported

### Step 5: Celery Services
- [ ] Start Redis server: `redis-server` (if local)
- [ ] Start Celery worker: `celery -A tasks.celery_app worker --loglevel=info`
- [ ] Start Celery Beat: `celery -A tasks.celery_app beat --loglevel=info`
- [ ] Verify scheduled tasks appear in logs
- [ ] Confirm metrics collection starts (every 5 seconds)

### Step 6: API Endpoint Testing
- [ ] Test system metrics: `GET /api/v1/api/system/metrics`
- [ ] Test system logs: `GET /api/v1/api/system/logs`
- [ ] Test task statuses: `GET /api/v1/api/system/tasks`
- [ ] Test system health: `GET /api/v1/api/system/health`
- [ ] Verify all endpoints require JWT auth (test without token)

### Step 7: WebSocket Testing
- [ ] Connect to WebSocket: `ws://localhost:8000/ws/system/logs`
- [ ] Verify initial connection message received
- [ ] Monitor periodic updates (every 5 seconds)
- [ ] Send filter command (JSON)
- [ ] Verify disconnect handling

### Step 8: Database Verification
- [ ] Check system_logs table: `SELECT COUNT(*) FROM system_logs;`
- [ ] Check system_metrics table: `SELECT COUNT(*) FROM system_metrics;`
- [ ] Check task_status table: `SELECT COUNT(*) FROM task_status;`
- [ ] Verify recent entries (from last 5 minutes)

### Step 9: Background Tasks Verification
- [ ] Confirm metrics collected (system_metrics table populated)
- [ ] Verify task statuses synced (task_status table has entries)
- [ ] Check task execution logs appear in system_logs
- [ ] Monitor Celery worker tasks

### Step 10: Error Handling
- [ ] Test with invalid JWT token → 401 response
- [ ] Test with missing parameters → 422 response
- [ ] Test with invalid metric query → appropriate error
- [ ] Verify error messages in logs
- [ ] Check WebSocket error messages

## 📊 Post-Deploy Validation

### Health Checks
- [ ] All 7 REST endpoints responding with 200 OK (with auth)
- [ ] WebSocket connection established and receiving updates
- [ ] Database tables populated with metrics
- [ ] Celery tasks running on schedule
- [ ] No errors in application logs
- [ ] No database connection issues

### Metrics Collection
- [ ] CPU metrics collected: ✓ (or "-" if unavailable)
- [ ] Memory metrics collected: ✓ (or "-" if unavailable)
- [ ] Disk metrics collected: ✓ (or "-" if unavailable)
- [ ] Container status tracked: ✓
- [ ] Task health synced: ✓
- [ ] Timestamp accuracy verified

### Task Health Status (CRITICAL)
- [ ] Task statuses appear in database
- [ ] Each expected task shows correct status (online/offline)
- [ ] Status updates every 30 seconds
- [ ] Health flag accurate based on Celery state
- [ ] Last execution times tracking correctly

### Logging
- [ ] System logs created by middleware
- [ ] Task execution logs captured
- [ ] Error logs recorded
- [ ] Log filtering works (by category, level, task)
- [ ] Pagination working on log endpoint

### Cleanup Tasks
- [ ] Daily cleanup tasks scheduled
- [ ] 30-day retention policy configured
- [ ] Old logs can be manually cleaned
- [ ] Old metrics can be manually cleaned

## 🔄 Rollback Plan

If deployment fails:
1. Stop Celery Beat and Worker: `pkill celery`
2. Stop FastAPI: `Ctrl+C`
3. Rollback migration: `alembic downgrade 006`
4. Revert requirements.txt to previous version
5. Restart services with previous code
6. Verify system operational

## ⚠️ Deployment Risks & Mitigation

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Database migration fails | HIGH | Test migration on dev first, have downgrade ready |
| Docker SDK unavailable | MEDIUM | Returns null metrics gracefully, "-" in UI |
| psutil unavailable | MEDIUM | Returns null metrics gracefully, "-" in UI |
| Celery tasks don't start | HIGH | Verify Redis, check Beat schedule, restart worker |
| High metrics storage growth | MEDIUM | 30-day retention + cleanup tasks manage data |
| WebSocket connection unstable | MEDIUM | Client-side reconnection logic, server ping/pong |

## ✨ Success Criteria

✅ **Deployment is successful when:**
1. All 7 API endpoints return 200 OK (with JWT)
2. WebSocket connects and receives 5-second updates
3. Database tables have data (metrics, logs, tasks)
4. Celery Beat shows 4 system monitoring tasks scheduled
5. Task health status page shows all tasks (online/offline)
6. No errors in application or Celery logs

✅ **Phase 1 is ready for Phase 2 when:**
1. Backend successfully deployed
2. Metrics being collected continuously
3. Task health status tracking accurately
4. All endpoints tested and working
5. WebSocket streaming functional

---

**Next Action After Deploy:** Proceed to Phase 2 - Frontend Vue 3 Dashboard Implementation
