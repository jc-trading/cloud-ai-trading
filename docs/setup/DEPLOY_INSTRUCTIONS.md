# Phase 1 System Monitoring Backend - Deployment Instructions

## ⚠️ Important

The migration requires a running PostgreSQL database. Follow the instructions below based on your deployment environment.

---

## Option 1: Docker Compose Deployment (Local Development)

This is the easiest way to test Phase 1 locally.

### Prerequisites
- Docker and Docker Compose installed
- PostgreSQL and Redis containers running
- Backend running in Docker container

### Steps

#### 1.1 Update Docker Compose (if needed)
Ensure `docker-compose.yml` includes PostgreSQL and Redis services:

```yaml
services:
  db:
    image: postgres:15
    environment:
      POSTGRES_DB: cloud_ai_trading
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"

  redis:
    image: redis:7
    ports:
      - "6379:6379"

  backend:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@db:5432/cloud_ai_trading
      DATABASE_URL_SYNC: postgresql://postgres:postgres@db:5432/cloud_ai_trading
      REDIS_URL: redis://redis:6379/0
      # ... other settings
    ports:
      - "8000:8000"
    depends_on:
      - db
      - redis
```

#### 1.2 Start Services
```bash
cd /path/to/CloudAiTrading

# Stop any running containers
docker-compose down -v

# Start all services
docker-compose up -d

# Check services are running
docker-compose ps
```

#### 1.3 Verify Database Connection
```bash
# Connect to PostgreSQL
docker-compose exec db psql -U postgres -d cloud_ai_trading

# Inside psql:
\dt                    # List tables
\q                     # Quit
```

#### 1.4 Run Migration
```bash
# Inside backend container
docker-compose exec backend alembic upgrade head

# Or from host machine (if backend not containerized yet):
cd backend
PYTHONPATH=. alembic upgrade head
```

#### 1.5 Verify Migration
```bash
# Check tables created
docker-compose exec db psql -U postgres -d cloud_ai_trading -c "\dt system_*"

# Should show:
# system_logs
# system_metrics
# task_status
```

#### 1.6 Install Dependencies
```bash
# If backend is containerized, dependencies are in Dockerfile
# Otherwise, install manually:
cd backend
pip install -r requirements.txt
```

#### 1.7 Start Backend Services
```bash
# Terminal 1: FastAPI Backend
docker-compose exec backend uvicorn app.main:app --reload --host 0.0.0.0

# Terminal 2: Celery Worker
docker-compose exec backend celery -A tasks.celery_app worker --loglevel=info

# Terminal 3: Celery Beat
docker-compose exec backend celery -A tasks.celery_app beat --loglevel=info
```

#### 1.8 Test API
```bash
# Get JWT token (if you have a user)
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "user", "password": "pass"}'

# With token, test endpoints:
curl -H "Authorization: Bearer <TOKEN>" \
  http://localhost:8000/api/v1/api/system/metrics

curl -H "Authorization: Bearer <TOKEN>" \
  http://localhost:8000/api/v1/api/system/logs

curl -H "Authorization: Bearer <TOKEN>" \
  http://localhost:8000/api/v1/api/system/tasks
```

---

## Option 2: VPS Deployment (Production)

For deploying to a Virtual Private Server.

### Prerequisites
- Python 3.10+
- PostgreSQL 13+
- Redis 6+
- System package managers (apt for Ubuntu/Debian)

### Steps

#### 2.1 Connect to VPS
```bash
ssh user@your-vps-ip

# Navigate to project
cd /path/to/CloudAiTrading/backend
```

#### 2.2 Install System Dependencies
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y python3-pip python3-dev postgresql postgresql-contrib redis-server

# Verify installations
python3 --version
psql --version
redis-cli ping
```

#### 2.3 Create Python Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate

# Verify activation (you should see (venv) prefix in terminal)
```

#### 2.4 Install Python Dependencies
```bash
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# Verify new dependencies
pip show psutil docker
```

#### 2.5 Configure Database Connection
```bash
# Create .env file or update existing
nano .env

# Add/update these lines:
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/cloud_ai_trading
DATABASE_URL_SYNC=postgresql://postgres:postgres@localhost:5432/cloud_ai_trading
REDIS_URL=redis://localhost:6379/0
```

#### 2.6 Setup PostgreSQL
```bash
# Start PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql  # Auto-start on reboot

# Create database
sudo -u postgres createdb cloud_ai_trading

# Verify
sudo -u postgres psql cloud_ai_trading -c "\dt"
```

#### 2.7 Setup Redis
```bash
# Start Redis
sudo systemctl start redis-server
sudo systemctl enable redis-server  # Auto-start on reboot

# Test Redis
redis-cli ping
# Should return: PONG
```

#### 2.8 Run Migration
```bash
source venv/bin/activate
cd /path/to/CloudAiTrading/backend

PYTHONPATH=. python -m alembic upgrade head

# Verify tables created
psql cloud_ai_trading -c "\dt system_*"
```

#### 2.9 Setup Systemd Services (Optional but Recommended)

Create `/etc/systemd/system/cloudai-backend.service`:
```ini
[Unit]
Description=Cloud AI Trading Backend
After=network.target postgresql.service redis.service

[Service]
User=cloudai
WorkingDirectory=/path/to/CloudAiTrading/backend
Environment="PATH=/path/to/CloudAiTrading/backend/venv/bin"
ExecStart=/path/to/CloudAiTrading/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/cloudai-celery-worker.service`:
```ini
[Unit]
Description=Cloud AI Trading Celery Worker
After=network.target redis.service

[Service]
User=cloudai
WorkingDirectory=/path/to/CloudAiTrading/backend
Environment="PATH=/path/to/CloudAiTrading/backend/venv/bin"
ExecStart=/path/to/CloudAiTrading/backend/venv/bin/celery -A tasks.celery_app worker --loglevel=info
Restart=always

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/cloudai-celery-beat.service`:
```ini
[Unit]
Description=Cloud AI Trading Celery Beat
After=network.target redis.service

[Service]
User=cloudai
WorkingDirectory=/path/to/CloudAiTrading/backend
Environment="PATH=/path/to/CloudAiTrading/backend/venv/bin"
ExecStart=/path/to/CloudAiTrading/backend/venv/bin/celery -A tasks.celery_app beat --loglevel=info
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start services:
```bash
sudo systemctl daemon-reload
sudo systemctl enable cloudai-backend cloudai-celery-worker cloudai-celery-beat
sudo systemctl start cloudai-backend cloudai-celery-worker cloudai-celery-beat

# Check status
sudo systemctl status cloudai-backend
sudo systemctl status cloudai-celery-worker
sudo systemctl status cloudai-celery-beat
```

#### 2.10 Setup Nginx Reverse Proxy (Optional)

Create `/etc/nginx/sites-available/cloudai`:
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

Enable Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/cloudai /etc/nginx/sites-enabled/
sudo nginx -t  # Test config
sudo systemctl restart nginx
```

---

## Option 3: Manual Testing (This Environment)

Since we don't have a running database here, we can verify the code is correct:

### Already Completed ✅
- All Python files syntax validated
- All imports verified
- Migration file created
- Schemas defined
- Routes registered
- Dependencies added to requirements.txt
- Configuration updated

### What to Do in Your Environment
1. Follow Option 1 (Docker) or Option 2 (VPS)
2. Run the migration: `alembic upgrade head`
3. Test the endpoints
4. Monitor the logs

---

## Testing After Deployment

### Test 1: Database Tables
```bash
# Connect to database
psql cloud_ai_trading

# Check tables
\dt system_*

# Should show:
#  system_logs
#  system_metrics  
#  task_status
```

### Test 2: API Endpoints

First, get a JWT token:
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "test_user",
    "password": "test_password"
  }'

# Save the token from response
export TOKEN="<your_token>"
```

Then test endpoints:
```bash
# Get system metrics
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/api/system/metrics

# Get logs
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/api/system/logs?limit=10

# Get task statuses
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/api/system/tasks

# Get system health
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/api/system/health
```

### Test 3: WebSocket Connection
```bash
# Install wscat: npm install -g wscat

wscat -c ws://localhost:8000/ws/system/logs \
  --header "Authorization: Bearer $TOKEN"

# Should see periodic updates every 5 seconds
```

### Test 4: Celery Tasks
Check Celery logs for:
- `collect-system-metrics` running every 5 seconds
- `sync-task-statuses` running every 30 seconds
- Metrics appearing in system_metrics table
- Tasks appearing in task_status table

```bash
# Check system metrics table
psql cloud_ai_trading -c "SELECT COUNT(*) FROM system_metrics;"
# Should show increasing count

# Check task statuses
psql cloud_ai_trading -c "SELECT task_name, status FROM task_status;"
# Should show: generate_trading_signals, calculate_portfolio_stats, etc.
```

---

## Troubleshooting

### Database Connection Error
```
sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL
```
**Solution:** Ensure DATABASE_URL_SYNC is set correctly in .env

### Migration Failed
```
alembic.util.exc.CommandError: Target database is not up to date
```
**Solution:** 
1. Check database exists: `psql cloud_ai_trading`
2. Run migration again: `alembic upgrade head`
3. Check for existing migration: `psql cloud_ai_trading -c "SELECT * FROM alembic_version;"`

### Celery Tasks Not Running
```
No worker processes found
```
**Solution:**
1. Ensure Redis is running: `redis-cli ping`
2. Restart Celery worker: `celery -A tasks.celery_app worker`
3. Check logs for errors

### WebSocket Connection Refused
```
[Errno 111] Connection refused
```
**Solution:**
1. Ensure backend is running: `ps aux | grep uvicorn`
2. Check port 8000 is accessible
3. Verify WebSocket endpoint is registered in routes.py

---

## Next Steps

Once Phase 1 is deployed and verified:

1. ✅ Phase 1 Complete: Backend system monitoring deployed
2. → **Phase 2**: Create Vue 3 frontend dashboard
3. → Phase 3: Integration testing and optimization

---

## Support

For issues with deployment:
1. Check the logs: `docker-compose logs backend` (Docker) or `journalctl -u cloudai-backend` (VPS)
2. Review PHASE_1_VALIDATION.md for component details
3. Verify all environment variables are set
4. Ensure database connectivity: `psql cloud_ai_trading`
5. Ensure Redis connectivity: `redis-cli ping`
