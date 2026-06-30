# Phase 1 Backend 完整测试指南

## 🎯 测试目标
验证所有Phase 1后端组件是否正常运行：
- ✅ Docker Compose服务启动
- ✅ 数据库迁移成功
- ✅ API端点可访问（需要JWT token）
- ✅ Celery任务正确注册
- ✅ WebSocket连接可用
- ✅ 数据库数据持久化
- ✅ 系统监控数据收集

---

## 📋 测试前准备

### 1. 清理旧容器并启动新服务

```bash
# 进入项目目录
cd ~/CloudAiTrading

# 停止并清理所有容器和数据
docker compose down -v

# 启动所有服务
docker compose up -d

# 等待服务启动
sleep 15

# 验证所有服务都在运行
docker compose ps

# 应该看到:
# ✅ cat_postgres       Healthy
# ✅ cat_redis         Healthy  
# ✅ cat_backend       Started
# ✅ cat_celery_worker Started
# ✅ cat_celery_beat   Started
```

### 2. 运行数据库迁移

```bash
docker compose exec backend alembic upgrade head

# 应该看到所有迁移成功运行：
# Running upgrade --> 001_initial
# Running upgrade 001_initial --> 002_watchlist_market_type
# ... (其他迁移)
# Running upgrade 005 --> 006
```

### 3. 验证表创建

```bash
docker compose exec db psql -U postgres -d cloud_ai_trading -c "\dt system_*"

# 应该看到3个新表:
#  system_logs
#  system_metrics
#  task_status
```

---

## 🧪 完整测试流程

### 第一步：验证Celery任务注册

```bash
# Terminal 1: 启动Celery Worker并查看任务
docker compose exec backend celery -A tasks.celery_app worker -l info

# 在日志中查找 [tasks] 部分，应该看到：
# ✅ collect_system_metrics      ← 我们新增的
# ✅ sync_task_statuses          ← 我们新增的
# ✅ cleanup_old_logs            ← 我们新增的
# ✅ cleanup_old_metrics         ← 我们新增的
# ✅ cleanup_market_data
# ✅ collect_market_data
# ✅ update_indicators
# ... (其他任务)

# 还应该看到:
# [2026-04-13 ...] INFO/MainProcess] Connected to redis://redis:6379/1
# [2026-04-13 ...] INFO/MainProcess] mingle: sync with 1 nodes
# [2026-04-13 ...] INFO/MainProcess] mingle: sync complete
```

### 第二步：启动Celery Beat

```bash
# Terminal 2: 启动Celery Beat调度器
docker compose exec backend celery -A tasks.celery_app beat -l info

# 应该看到:
# celery beat v5.x.x (recovery)
# Configuration ->
#   . broker -> redis://redis:6379/1
#   . scheduler -> celery.beat.PersistentScheduler
# 
# [2026-04-13 ...] INFO/MainProcess] beat: Starting...
# [2026-04-13 ...] INFO/MainProcess] Scheduler: Sending due task collect-system-metrics
# [2026-04-13 ...] INFO/MainProcess] Scheduler: Sending due task sync-task-statuses
# ... (每5秒和30秒运行一次)
```

### 第三步：获取JWT Token

```bash
# Terminal 3: 获取测试token
# 首先需要创建一个用户或使用现有用户

curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "test_user",
    "password": "test_password"
  }'

# 响应应该包含 "access_token"
# 保存这个token（比如导出到环境变量）
export TOKEN="your_token_here"
```

### 第四步：测试API端点

#### 测试1: 获取系统指标 (GET /api/v1/system/metrics)

```bash
curl -X GET http://localhost:8000/api/v1/system/metrics \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json"

# 预期响应 (200 OK):
# {
#   "timestamp": "2026-04-13T13:30:45.123456+00:00",
#   "cpu_percent": 45.2,           # 或 null 如果无法获取
#   "memory_percent": 62.5,
#   "disk_percent": 38.1,
#   "active_task_count": 5,
#   "failed_task_count": 0,
#   "scheduled_task_count": 8,
#   "alerts": []
# }

# ✅ 成功标志：
# - HTTP 200
# - 包含 timestamp
# - 包含 cpu/memory/disk percent（或null）
# - 包含 task counts
```

#### 测试2: 获取系统日志 (GET /api/v1/system/logs)

```bash
curl -X GET "http://localhost:8000/api/v1/system/logs?limit=10&offset=0" \
  -H "Authorization: Bearer $TOKEN"

# 预期响应 (200 OK):
# {
#   "logs": [
#     {
#       "id": "uuid",
#       "timestamp": "2026-04-13T13:30:45+00:00",
#       "category": "system",    # or "market_data", "trading", "schedule"
#       "level": "INFO",         # or "WARNING", "ERROR", etc
#       "message": "Connected to redis://redis:6379/1",
#       "task_name": null,
#       "event_metadata": null,
#       ...
#     }
#   ],
#   "total": 15,
#   "limit": 10,
#   "offset": 0
# }

# ✅ 成功标志：
# - HTTP 200
# - logs数组非空
# - 每条日志包含所有字段
# - total和limit显示正确
```

#### 测试3: 获取任务状态 (GET /api/v1/system/tasks)

```bash
curl -X GET http://localhost:8000/api/v1/system/tasks \
  -H "Authorization: Bearer $TOKEN"

# 预期响应 (200 OK):
# {
#   "tasks": [
#     {
#       "id": "uuid",
#       "task_name": "collect_system_metrics",
#       "description": null,
#       "status": "online",            # ✅ 我们的新任务应该显示 online
#       "is_healthy": true,
#       "last_execution_time": "2026-04-13T13:30:40+00:00",
#       "last_execution_duration_ms": 250,
#       "next_execution_time": "2026-04-13T13:30:45+00:00",
#       "total_executions": 8,
#       "failed_executions": 0,
#       "success_rate": 100.00,
#       ...
#     },
#     {
#       "task_name": "sync_task_statuses",
#       "status": "online",
#       ...
#     },
#     {
#       "task_name": "collect_market_data",
#       "status": "online",  # 如果Celery worker运行正常
#       ...
#     },
#     ... (其他任务)
#   ],
#   "total": 9,
#   "healthy_count": 9,
#   "unhealthy_count": 0
# }

# ✅ 成功标志：
# - HTTP 200
# - 包含至少4个我们新增的任务 (collect_system_metrics, sync_task_statuses等)
# - 每个任务的status为"online"
# - healthy_count > 0
```

#### 测试4: 获取系统健康状态 (GET /api/v1/system/health)

```bash
curl -X GET http://localhost:8000/api/v1/system/health \
  -H "Authorization: Bearer $TOKEN"

# 预期响应 (200 OK):
# {
#   "timestamp": "2026-04-13T13:30:45.123456+00:00",
#   "system_metrics": {
#     "id": "uuid",
#     "timestamp": "...",
#     "cpu_percent": 45.2,
#     "memory_percent": 62.5,
#     "disk_percent": 38.1,
#     ...
#   },
#   "cpu_metrics": {
#     "percent": 45.2,
#     "cores": 8,
#     "load_average": {...}
#   },
#   "memory_metrics": {...},
#   "disk_metrics": {...},
#   "task_statuses": [...],   # 所有任务状态
#   "service_health": {
#     "postgres": "online",
#     "redis": "online",
#     "celery_worker": "online",
#     "celery_beat": "online"
#   },
#   "alerts": [],             # 如果有警告会显示在这里
#   "is_healthy": true        # ✅ 整体系统健康状态
# }

# ✅ 成功标志：
# - HTTP 200
# - is_healthy: true
# - service_health显示所有服务online
# - alerts为空（或只有可接受的警告）
```

#### 测试5: 同步任务状态 (POST /api/v1/system/tasks/sync)

```bash
curl -X POST http://localhost:8000/api/v1/system/tasks/sync \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json"

# 预期响应 (200 OK):
# {
#   "status": "success",
#   "message": "Task statuses synced",
#   "tasks": {
#     "collect_system_metrics": "online",
#     "sync_task_statuses": "online",
#     "cleanup_old_logs": "online",
#     "cleanup_old_metrics": "online",
#     "generate_trading_signals": "online",
#     ...
#   }
# }

# ✅ 成功标志：
# - HTTP 200
# - status: "success"
# - 所有任务状态都是 "online"
```

### 第五步：测试WebSocket实时流

```bash
# 需要wscat工具
npm install -g wscat

# 或者使用Python测试WebSocket
python3 << 'EOF'
import asyncio
import websockets
import json

async def test_websocket():
    token = "your_token_here"
    uri = f"ws://localhost:8000/api/v1/system/ws/logs?token={token}"
    
    async with websockets.connect(uri) as websocket:
        # 接收初始消息
        msg = await websocket.recv()
        print("初始连接消息:", msg)
        
        # 接收更新（每5秒一次）
        for i in range(3):
            msg = await websocket.recv()
            data = json.loads(msg)
            print(f"\n更新 #{i+1}:")
            print(f"  时间: {data.get('timestamp')}")
            print(f"  类型: {data.get('type')}")
            print(f"  metrics CPU: {data.get('metrics', {}).get('cpu_percent')}%")

asyncio.run(test_websocket())
EOF

# 预期输出:
# 初始连接消息: {"type": "connected", "message": "Connected to system monitoring stream", ...}
# 
# 更新 #1:
#   时间: 2026-04-13T13:30:50...
#   类型: update
#   metrics CPU: 45.2%
#
# 更新 #2:
#   时间: 2026-04-13T13:30:55...
#   ...

# ✅ 成功标志：
# - 初始连接成功
# - 每5秒收到一个更新
# - 更新包含metrics和alerts信息
```

### 第六步：验证数据库数据

```bash
# 检查system_logs表
docker compose exec db psql -U postgres -d cloud_ai_trading << EOF
SELECT COUNT(*) as log_count FROM system_logs;
SELECT COUNT(*) as metric_count FROM system_metrics;
SELECT COUNT(*) as task_count FROM task_status;

-- 查看最近的日志
SELECT timestamp, category, level, message FROM system_logs 
ORDER BY timestamp DESC LIMIT 5;

-- 查看最近的指标
SELECT timestamp, cpu_percent, memory_percent, disk_percent FROM system_metrics 
ORDER BY timestamp DESC LIMIT 3;

-- 查看任务状态
SELECT task_name, status, is_healthy, success_rate FROM task_status 
ORDER BY task_name;
EOF

# ✅ 成功标志：
# - log_count > 0 (有日志被记录)
# - metric_count > 0 (有指标被保存，每5秒一条)
# - task_count >= 9 (至少9个任务状态)
# - 最近的日志显示系统活动
# - 最近的指标显示系统资源使用情况
```

---

## ✅ 完整测试检查清单

运行完上述所有测试后，检查以下项目：

- [ ] Docker Compose所有服务都在运行
- [ ] 数据库迁移成功完成
- [ ] 3个system_*表已创建
- [ ] Celery Worker显示4个新任务已注册
- [ ] Celery Beat正在调度任务
- [ ] API /metrics端点返回200和系统指标
- [ ] API /logs端点返回日志列表
- [ ] API /tasks端点显示所有任务状态为online
- [ ] API /health端点显示is_healthy: true
- [ ] /tasks/sync端点成功同步任务状态
- [ ] WebSocket /ws/logs连接成功并每5秒收到更新
- [ ] 数据库中有日志和指标数据
- [ ] 没有明显的错误日志

---

## 🐛 常见问题排查

### 问题1: "service db is not running"
**解决方案:**
```bash
docker compose restart db
sleep 10
```

### 问题2: Celery任务未注册
**解决方案:**
```bash
# 检查system_tasks.py是否有导入错误
docker compose exec backend python -c "from tasks import system_tasks; print('OK')"

# 重启worker
docker compose restart backend
```

### 问题3: WebSocket连接失败 (401/403)
**解决方案:**
- 检查token是否有效
- 确保用户拥有"manage_system"权限
- 检查token未过期

### 问题4: 数据库表为空
**解决方案:**
```bash
# 等待Celery任务运行（第一次运行在5秒后）
sleep 10

# 再次检查
docker compose exec db psql -U postgres -d cloud_ai_trading -c \
  "SELECT COUNT(*) FROM system_metrics;"
```

---

## 📊 期望的数据增长

| 时间 | system_logs | system_metrics | task_status |
|------|------------|-----------------|------------|
| T+0 (启动) | 0 | 0 | 0 |
| T+5秒 | 1-5 | 1 | 9 |
| T+30秒 | 5-15 | 6-7 | 9 |
| T+1分钟 | 10-20 | 12-14 | 9 |
| T+5分钟 | 30-50+ | 60+ | 9 |

---

## ✨ 成功标准

**Phase 1后端测试成功 = 以下全部通过:**

✅ 所有4个系统监控API端点(metrics, logs, tasks, health)返回200
✅ 所有4个新Celery任务都显示为"online"
✅ WebSocket成功连接并每5秒发送更新
✅ 数据库表中有实际的系统监控数据
✅ 没有明显的错误日志
✅ CPU、内存、磁盘指标正确显示
✅ 任务健康状态被正确追踪

---

**完成测试后，就可以开始Phase 2前端开发了！** 🚀
