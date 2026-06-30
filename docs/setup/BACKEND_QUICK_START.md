# CloudAiTrading Backend - Quick Start Guide

## Prerequisites

- Docker & Docker Compose
- Python 3.12 (for local development)
- Git

## 5-Minute Setup

### 1. Clone & Setup Environment

```bash
cd CloudAiTrading

# Copy example env and fill in secrets
cp .env.example .env

# Generate secure keys
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"

# Add keys to .env
```

### 2. Start Services

```bash
# Start all services (postgres, redis, backend, celery)
docker compose up -d

# Watch logs
docker compose logs -f backend
```

### 3. Initialize Database

```bash
# Migrations run automatically on backend startup
# Or manually:
docker compose exec backend alembic upgrade head
```

### 4. Test API

```bash
# Health check
curl http://localhost:8000/api/health

# Register user
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test User",
    "email": "test@example.com",
    "password": "SecurePass123"
  }'

# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "SecurePass123"
  }'

# Copy access_token from response and use it
curl http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### 5. View Swagger Docs

Open browser: http://localhost:8000/api/docs

---

## Development Workflow

### Adding a New Endpoint

1. **Create schema** in `app/modules/{module}/schemas.py`
   ```python
   from pydantic import BaseModel, Field
   
   class MyRequest(BaseModel):
       name: str = Field(..., min_length=1)
       value: int = Field(default=0)
   ```

2. **Add service method** in `app/modules/{module}/service.py`
   ```python
   @staticmethod
   async def my_operation(db: AsyncSession, data: MyRequest):
       # Database operations
       return result
   ```

3. **Add router endpoint** in `app/modules/{module}/router.py`
   ```python
   @router.post("/my-endpoint")
   async def my_endpoint(data: MyRequest, current_user: CurrentUser, db: DB):
       return await MyService.my_operation(db, data)
   ```

4. **Restart backend**
   ```bash
   docker compose restart backend
   ```

### Database Migrations

```bash
# Auto-generate migration from model changes
docker compose exec backend alembic revision --autogenerate -m "Add new column"

# Review the generated migration file
# Then apply it
docker compose exec backend alembic upgrade head
```

### Running Background Tasks

```bash
# Check Celery worker status
docker compose exec celery-worker celery -A tasks.celery_app inspect active

# View registered tasks
docker compose exec celery-worker celery -A tasks.celery_app inspect registered

# Manually trigger a task (from Python shell inside container)
docker compose exec backend python -c "
from tasks.celery_app import celery_app
from tasks.market_tasks import pull_market_data
result = pull_market_data.delay()
print(result.get())
"
```

### Debug Mode

```bash
# Enable debug logs
# Edit .env: DEBUG=True

# Restart backend
docker compose restart backend

# View swagger docs (enabled in debug mode)
# http://localhost:8000/api/docs
```

---

## Project Structure

```
backend/
├── app/
│   ├── main.py                 # FastAPI app entry point
│   ├── config.py               # Environment configuration
│   ├── database.py             # SQLAlchemy setup
│   ├── dependencies.py         # JWT & role dependencies
│   ├── core/                   # Security, exceptions, middleware
│   └── modules/
│       ├── auth/               # User management, JWT
│       ├── exchange/           # Exchange integrations
│       ├── market/             # Market data (OHLCV)
│       ├── watchlist/          # User watchlists
│       ├── analysis/           # AI analysis results
│       ├── trading/            # Trade execution
│       ├── strategy/           # User strategies
│       └── admin/              # System admin
├── migrations/                 # Alembic database versions
│   └── versions/               # Migration files
├── tasks/                      # Celery background jobs
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Container image
├── alembic.ini                 # Alembic config
└── .env.example               # Environment template
```

---

## Common API Patterns

### Protected Endpoints (Require Login)

```python
from app.dependencies import CurrentUser, DB

@router.get("/my-data")
async def get_my_data(current_user: CurrentUser, db: DB):
    # current_user is the authenticated User object
    # db is the AsyncSession
    return {"user_id": current_user.id, "email": current_user.email}
```

### Admin-Only Endpoints

```python
from app.dependencies import AdminUser, DB

@router.delete("/users/{user_id}")
async def delete_user(user_id: str, admin: AdminUser, db: DB):
    # Only SUPER_ADMIN or ADMIN can access
    return await AuthService.delete_user(db, UUID(user_id))
```

### Permission-Based Endpoints

```python
from app.dependencies import require_permission, CurrentUser, DB

@router.post("/trading/live-order")
async def place_live_order(
    data: OrderRequest,
    current_user: CurrentUser = Depends(require_permission("live_trading")),
    db: DB = Depends(get_db)
):
    # Only users with "live_trading" permission
    return await TradingService.place_order(db, current_user, data)
```

### Pagination

```python
@router.get("/users")
async def list_users(db: DB, skip: int = 0, limit: int = 50):
    users = await AuthService.list_users(db, skip, limit)
    return users
```

### Error Handling

```python
from app.core.exceptions import NotFoundException, PermissionDeniedException

@router.get("/users/{user_id}")
async def get_user(user_id: str, db: DB):
    try:
        user = await AuthService.get_user_by_id(db, UUID(user_id))
        return user
    except NotFoundException as e:
        raise e  # Already raises 404
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
```

---

## Testing

### Unit Tests (Local)

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest backend/tests/

# With coverage
pytest --cov=app backend/tests/
```

### Integration Tests (Docker)

```bash
# Create test user
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","email":"test@test.com","password":"Test123!"}'

# Test protected endpoint
curl http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer TOKEN"
```

---

## Troubleshooting

### Backend won't start

```bash
# Check logs
docker compose logs backend

# Common issues:
# - Missing .env file → copy from .env.example
# - Database not running → docker compose up postgres
# - Port 8000 in use → docker compose down && up
```

### Database connection error

```bash
# Check if postgres is healthy
docker compose ps postgres

# If unhealthy, reset
docker compose down -v
docker compose up -d postgres
docker compose up -d backend
```

### Celery tasks not running

```bash
# Check worker is alive
docker compose exec celery-worker celery -A tasks.celery_app inspect ping

# Check for errors
docker compose logs celery-worker

# Restart workers
docker compose restart celery-worker celery-beat
```

### Authentication not working

```bash
# Check SECRET_KEY is set
grep SECRET_KEY .env

# Check token in Authorization header format
# Should be: Authorization: Bearer {token}

# Decode token to inspect (if needed)
# Don't share tokens in logs!
```

---

## Production Deployment

### Before Going Live

1. Change `DEBUG=False` in .env
2. Generate new `SECRET_KEY` and `ENCRYPTION_KEY`
3. Set strong `DB_PASSWORD`
4. Configure `CORS_ORIGINS` for your domain
5. Enable HTTPS/TLS
6. Set up PostgreSQL backups
7. Set up Redis persistence
8. Configure application monitoring

### Environment Variables (Production)

```env
ENVIRONMENT=production
DEBUG=False
LOG_LEVEL=INFO

# Use strong, randomly generated values
SECRET_KEY=<very-long-random-string>
ENCRYPTION_KEY=<fernet-key>
DB_PASSWORD=<strong-password>

# APIs
ANTHROPIC_API_KEY=sk-ant-xxxxx
ALPACA_API_KEY=PKXXXXXX
ALPACA_API_SECRET=xxxxxxxx
```

### Scaling

```bash
# Multiple backend instances (use load balancer)
docker compose up -d --scale backend=3

# Multiple Celery workers
docker compose up -d --scale celery-worker=5

# (In production, use Kubernetes or Docker Swarm)
```

---

## Resources

- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [SQLAlchemy Async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Alembic Docs](https://alembic.sqlalchemy.org/)
- [Celery Docs](https://docs.celeryproject.org/)
- [Pydantic V2](https://docs.pydantic.dev/latest/)

---

## Support

For issues or questions:
1. Check logs: `docker compose logs -f`
2. Review API docs: http://localhost:8000/api/docs
3. Check environment: `grep -v "^#" .env`

---

Last Updated: April 12, 2026
