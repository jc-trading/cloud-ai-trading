# Cloud AI Trading — Local Setup Guide (MVP v1)

> **Purpose:** Get the system running locally in ~30 minutes.  
> **Target:** Linux + Docker + PostgreSQL on developer's machine.  
> **Time to Ready:** ~30 min (after Docker + Node.js installed)

---

## Prerequisites

Install these ONCE on your system (if not already done):

- **Docker** v4.0+ & **Docker Compose** v2.0+  
  → Install: https://docs.docker.com/desktop
- **Node.js** 18+ & **npm** 9+  
  → Install: https://nodejs.org
- **Git** (for version control)  
  → Install: https://git-scm.com
- **Python** 3.11+ (for local development only, optional)

**Check installed:**
```bash
docker --version && docker compose version && node --version && npm --version
```

---

## Step 1: Clone & Navigate to Project

```bash
git clone <your-repo-url> CloudAiTrading
cd CloudAiTrading
```

---

## Step 2: Create Environment File

```bash
cp .env.example .env
```

Edit `.env` with your own values:

```bash
# Generate a secure SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Generate a FERNET encryption key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Complete `.env` file:**

```env
# App
SECRET_KEY=<paste-generated-secret-key-here>
ENCRYPTION_KEY=<paste-generated-fernet-key-here>
DEBUG=False
ENVIRONMENT=local

# Database
DATABASE_URL=postgresql://postgres:mysecurepassword@postgres:5432/cloudaitrading
DB_PASSWORD=mysecurepassword

# API Keys
ANTHROPIC_API_KEY=sk-ant-<your-key-here>
BINANCE_API_KEY=<optional-get-later>
BINANCE_API_SECRET=<optional-get-later>

# Telegram (optional, Phase 5)
TELEGRAM_BOT_TOKEN=<optional-get-later>
TELEGRAM_CHAT_ID=<optional-get-later>

# Redis
REDIS_URL=redis://redis:6379

# Logging
LOG_LEVEL=INFO
```

> ⚠️ **Security:** Never commit `.env` to git. It's in `.gitignore` already.

---

## Step 3: Start Docker Services

```bash
docker compose up -d
```

This starts 5 containers in the background:

| Service | Container | Port | Purpose |
|---------|-----------|------|---------|
| Backend | cat_backend | 8000 | FastAPI + Uvicorn |
| Celery Worker | cat_celery_worker | — | AI analysis, trading, notifications |
| Celery Beat | cat_celery_beat | — | Scheduled tasks |
| PostgreSQL | cat_postgres | 5432 | Main database |
| Redis | cat_redis | 6379 | Cache + Celery broker |

**Verify all services are healthy:**

```bash
docker compose ps
```

You should see 5 containers with status `Up` (or `healthy`).

**View logs in real-time:**

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f celery-worker
```

---

## Step 4: Run Database Migrations

```bash
docker compose exec backend alembic upgrade head
```

This:
- Creates all 11 database tables
- Seeds a SUPER_ADMIN user

**Default super admin account:**
- Email: `admin@cloudaitrading.local`
- Password: `Abc1234#`

> ⚠️ Change this password immediately after first login!

---

## Step 5: Verify Backend

Open your browser:

### ✅ Health Check
http://localhost:8000/api/health  
Should return: `{"status": "ok"}`

### ✅ Swagger API Docs
http://localhost:8000/api/docs  
Interactive API documentation

### ✅ Test Login

1. In Swagger, go to `POST /api/v1/auth/login`
2. Click "Try it out"
3. Enter body:
   ```json
   {
     "email": "admin@cloudaitrading.local",
     "password": "Abc1234#"
   }
   ```
4. Click "Execute"
5. You should get back:
   ```json
   {
     "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
     "token_type": "Bearer",
     "expires_in": 3600
   }
   ```

---

## Step 6: Start Frontend Development Server

```bash
cd frontend
npm install   # One-time dependency install
npm run dev   # Start dev server (port 5173)
```

Frontend runs at: **http://localhost:5173**

Login with:
- Email: `admin@cloudaitrading.local`
- Password: `Abc1234#`

---

## Step 7: Explore the System

### Backend Endpoints

All endpoints are prefixed with `/api/v1/` (see `FUNCTIONAL_SPEC.md` for full list):

```bash
# Market data
curl http://localhost:8000/api/v1/market/tickers

# Market details
curl http://localhost:8000/api/v1/market/BTCUSDT

# Watchlist
curl http://localhost:8000/api/v1/watchlist

# AI analysis (empty until Phase 3)
curl http://localhost:8000/api/v1/analysis
```

### Frontend Pages

- **Login:** http://localhost:5173/login
- **Dashboard:** http://localhost:5173/ (shows placeholder stats)
- **Market:** http://localhost:5173/market (shows mock prices)
- **Watchlist:** http://localhost:5173/watchlist (empty until symbols added)

---

## Step 8: Add Binance API Keys (Optional, Phase 2+)

When ready to fetch real Binance data:

1. Go to **Binance Account** → **API Management**
   - https://www.binance.com/en/account/api-management
2. Create a new API Key with **read-only** + **spot trading** permissions
3. Restrict to your IP address (security best practice)
4. **DO NOT enable withdrawal permissions**
5. Copy API Key and Secret
6. Add to `.env`:
   ```env
   BINANCE_API_KEY=<your-key>
   BINANCE_API_SECRET=<your-secret>
   ```
7. Rebuild backend:
   ```bash
   docker compose up -d --build backend
   ```

---

## Step 9: Add Telegram Bot (Optional, Phase 5+)

When ready for notifications:

1. Open Telegram, search for **@BotFather**
2. Send `/newbot` and follow instructions
3. Copy the **Bot Token** (e.g., `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
4. Get your **Chat ID**:
   - Send a message to your new bot
   - Visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Find your `chat.id`
5. Add to `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=<bot-token>
   TELEGRAM_CHAT_ID=<your-chat-id>
   ```
6. Rebuild:
   ```bash
   docker compose up -d --build backend
   ```

---

## Troubleshooting

### Backend won't start

```bash
docker compose logs backend
```

**Common issues:**
- `.env` file missing or incomplete
- Database password contains special characters (needs escaping in DATABASE_URL)
- Port 8000 already in use

### Migration fails

```bash
docker compose exec backend alembic current
docker compose exec backend alembic upgrade head --sql  # Dry run
```

### Celery tasks not running

```bash
docker compose logs celery-worker
docker compose logs celery-beat

# Check Redis connection
docker compose exec redis redis-cli ping
```

Should return `PONG`.

### Reset everything (WARNING: deletes all data)

```bash
docker compose down -v  # Remove containers + volumes
docker compose up -d    # Start fresh
docker compose exec backend alembic upgrade head
```

### Port already in use

If `docker compose up` fails with "Port X already in use":

```bash
# Find what's using port 8000
lsof -i :8000

# Change port in docker-compose.yml
# services > backend > ports > change "8000:8000"
```

---

## Development Workflow

### Make a code change

```bash
# Backend changes (auto-reload)
# Just edit backend/app/*.py, service restarts automatically

# Frontend changes (hot reload)
# Just edit frontend/src/*, browser auto-refreshes
```

### Run tests (Phase 2+)

```bash
# Unit tests
docker compose exec backend pytest tests/

# Integration tests (requires live services)
docker compose exec backend pytest tests/ -m integration
```

### Check logs

```bash
# Real-time all logs
docker compose logs -f

# Specific service, last 100 lines
docker compose logs backend --tail 100

# Follow backend with timestamps
docker compose logs -f --timestamps backend
```

---

## Database Access (Optional)

If you want to directly inspect the database:

### Via pgAdmin (Web UI)
1. Access: http://localhost:5050 (if pgAdmin container is added to docker-compose)
2. Login with credentials from docker-compose.yml
3. Add PostgreSQL server: host=`postgres`, port=`5432`

### Via psql (Command line)
```bash
docker compose exec postgres psql -U postgres -d cloudaitrading

# List tables
\dt

# Query users
SELECT * FROM users;

# Exit
\q
```

---

## Next Steps

After setup is successful:

1. ✅ **Phase 1 Complete:** Docker + Auth + DB ready
2. ⏳ **Phase 2:** Add Binance WebSocket + indicators
3. ⏳ **Phase 3:** Build AI orchestrator (2 agents)
4. ⏳ **Phase 4:** Implement simulate engine
5. ⏳ **Phase 5:** Add Telegram + dashboard
6. ⏳ **Phase 6+:** News, stability, VPS migration

For task details, see `PROGRESS.md`.

---

## Production Deployment (Phase 8)

When ready to deploy to **Vultr Tokyo VPS** (after all phases):

```bash
# On VPS
ssh root@your-vps-ip
curl -fsSL https://get.docker.com | sh
apt install docker-compose-plugin -y

# Clone repo
git clone <your-repo-url> /opt/CloudAiTrading
cd /opt/CloudAiTrading

# Configure production .env
cp .env.example .env
nano .env  # Add production values

# Build frontend
cd frontend && npm install && npm run build && cd ..

# Start services
docker compose up -d --build

# Run migrations
docker compose exec backend alembic upgrade head
```

For full VPS setup, see `CloudAiTrading-System-Plan.md` §8 (Running Modes).

---

## Support & Issues

If something breaks:

1. **Check logs:** `docker compose logs <service>`
2. **Verify .env:** All required keys present and valid
3. **Verify Docker:** `docker ps`, `docker network ls`
4. **Restart services:** `docker compose restart`
5. **Ask for help:** Provide logs + error messages

---

**Setup Complete! 🎉**

Your system is now ready for **Phase 1 development**. See `PROGRESS.md` for what comes next.

---

**End of Setup Guide — v2.0**
