#!/bin/bash

##############################################################################
# Phase 1 System Monitoring Backend - Automated Deployment Script
#
# Usage:
#   ./deploy.sh [option]
#
# Options:
#   all           Run full deployment (install, migrate, start)
#   install       Install dependencies
#   migrate       Run database migration
#   test-api      Test API endpoints
#   test-ws       Test WebSocket endpoint
#   start         Start all services
#   stop          Stop all services
#   status        Check service status
#   logs          Tail service logs
#
# Examples:
#   ./deploy.sh all
#   ./deploy.sh install
#   ./deploy.sh migrate
#   ./deploy.sh start
##############################################################################

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NAME="CloudAiTrading"
BACKEND_DIR="$SCRIPT_DIR"
VENV_DIR="$BACKEND_DIR/venv"
PYTHON_CMD="python"
DB_CHECK_MAX_RETRIES=30

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_requirements() {
    log_info "Checking requirements..."

    # Check Python
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 is not installed"
        exit 1
    fi
    PYTHON_CMD="python3"
    log_success "Python 3 found: $($PYTHON_CMD --version)"

    # Check PostgreSQL
    if ! command -v psql &> /dev/null; then
        log_warning "PostgreSQL client not found (psql). Database checks will be skipped."
    else
        log_success "PostgreSQL client found"
    fi

    # Check Redis
    if ! command -v redis-cli &> /dev/null; then
        log_warning "Redis client not found (redis-cli). Redis checks will be skipped."
    else
        log_success "Redis client found"
    fi
}

check_venv() {
    if [ -d "$VENV_DIR" ]; then
        log_info "Virtual environment found at $VENV_DIR"
        source "$VENV_DIR/bin/activate"
    else
        log_info "Virtual environment not found. Creating..."
        $PYTHON_CMD -m venv "$VENV_DIR"
        source "$VENV_DIR/bin/activate"
        log_success "Virtual environment created"
    fi
}

install_dependencies() {
    log_info "Installing dependencies..."

    check_venv

    # Upgrade pip
    pip install --upgrade pip setuptools wheel -q

    # Install requirements
    if [ -f "$BACKEND_DIR/requirements.txt" ]; then
        pip install -r "$BACKEND_DIR/requirements.txt" -q
        log_success "Dependencies installed successfully"
    else
        log_error "requirements.txt not found"
        exit 1
    fi

    # Verify new dependencies
    log_info "Verifying system monitoring dependencies..."
    pip show psutil > /dev/null && log_success "psutil installed"
    pip show docker > /dev/null && log_success "docker installed"
}

check_database_connection() {
    log_info "Checking database connection..."

    # Try to connect to database
    if command -v psql &> /dev/null; then
        DB_URL=${DATABASE_URL_SYNC:-"postgresql://postgres:postgres@localhost:5432/cloud_ai_trading"}

        # Extract connection details
        DB_USER=$(echo "$DB_URL" | grep -oP '(?<=://).*(?=:)' | tail -1)
        DB_HOST=$(echo "$DB_URL" | grep -oP '(?<=@).*(?=:)' | tail -1)
        DB_PORT=$(echo "$DB_URL" | grep -oP '(?<=:)\d+(?=/)' | tail -1)
        DB_NAME=$(echo "$DB_URL" | grep -oP '(?<=/)[^?]*' | tail -1)

        # Retry logic for database connection
        RETRY_COUNT=0
        until psql -h "${DB_HOST:-localhost}" -U "${DB_USER:-postgres}" -d "${DB_NAME:-cloud_ai_trading}" -c "SELECT 1" > /dev/null 2>&1; do
            RETRY_COUNT=$((RETRY_COUNT + 1))
            if [ $RETRY_COUNT -gt $DB_CHECK_MAX_RETRIES ]; then
                log_error "Database connection failed after $DB_CHECK_MAX_RETRIES retries"
                log_error "Please ensure PostgreSQL is running and accessible"
                log_error "Connection string: $DB_URL"
                exit 1
            fi
            echo -ne "\r${YELLOW}Waiting for database... ($RETRY_COUNT/$DB_CHECK_MAX_RETRIES)${NC}"
            sleep 1
        done
        echo ""
        log_success "Database connection successful"
    else
        log_warning "psql not found. Skipping database connection check."
    fi
}

run_migration() {
    log_info "Running database migration..."

    check_venv
    check_database_connection

    cd "$BACKEND_DIR"

    if [ -f "alembic.ini" ]; then
        log_info "Executing: alembic upgrade head"
        $PYTHON_CMD -m alembic upgrade head
        log_success "Database migration completed"

        # Verify tables were created
        if command -v psql &> /dev/null; then
            log_info "Verifying tables created..."
            DB_URL=${DATABASE_URL_SYNC:-"postgresql://postgres:postgres@localhost:5432/cloud_ai_trading"}
            DB_NAME=$(echo "$DB_URL" | grep -oP '(?<=/)[^?]*' | tail -1)

            TABLE_COUNT=$(psql -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'system_%';" 2>/dev/null || echo "0")

            if [ "$TABLE_COUNT" -eq "3" ]; then
                log_success "All 3 system tables created (system_logs, system_metrics, task_status)"
            else
                log_warning "Expected 3 system tables, found $TABLE_COUNT"
            fi
        fi
    else
        log_error "alembic.ini not found"
        exit 1
    fi
}

start_services() {
    log_info "Starting services..."

    check_venv

    # Check if services already running
    if pgrep -f "uvicorn.*app.main:app" > /dev/null; then
        log_warning "Backend service already running"
    else
        log_info "Starting FastAPI backend..."
        nohup $VENV_DIR/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 > "$BACKEND_DIR/backend.log" 2>&1 &
        BACKEND_PID=$!
        echo $BACKEND_PID > "$BACKEND_DIR/backend.pid"
        sleep 2
        log_success "Backend started (PID: $BACKEND_PID)"
    fi

    if pgrep -f "celery.*worker" > /dev/null; then
        log_warning "Celery worker already running"
    else
        log_info "Starting Celery worker..."
        nohup $VENV_DIR/bin/celery -A tasks.celery_app worker --loglevel=info > "$BACKEND_DIR/celery_worker.log" 2>&1 &
        WORKER_PID=$!
        echo $WORKER_PID > "$BACKEND_DIR/celery_worker.pid"
        sleep 2
        log_success "Celery worker started (PID: $WORKER_PID)"
    fi

    if pgrep -f "celery.*beat" > /dev/null; then
        log_warning "Celery beat already running"
    else
        log_info "Starting Celery beat..."
        nohup $VENV_DIR/bin/celery -A tasks.celery_app beat --loglevel=info > "$BACKEND_DIR/celery_beat.log" 2>&1 &
        BEAT_PID=$!
        echo $BEAT_PID > "$BACKEND_DIR/celery_beat.pid"
        sleep 2
        log_success "Celery beat started (PID: $BEAT_PID)"
    fi

    log_success "All services started"
    log_info "Backend: http://localhost:8000"
    log_info "API Docs: http://localhost:8000/api/docs"
}

stop_services() {
    log_info "Stopping services..."

    for PID_FILE in "$BACKEND_DIR"/{backend,celery_worker,celery_beat}.pid; do
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                log_info "Stopping process $PID..."
                kill "$PID" || true
                sleep 1
                kill -9 "$PID" 2>/dev/null || true
                rm "$PID_FILE"
                log_success "Process $PID stopped"
            fi
        fi
    done

    log_success "All services stopped"
}

check_status() {
    log_info "Checking service status..."

    echo ""

    if pgrep -f "uvicorn.*app.main:app" > /dev/null; then
        log_success "✓ Backend running on http://localhost:8000"
    else
        log_error "✗ Backend NOT running"
    fi

    if pgrep -f "celery.*worker" > /dev/null; then
        log_success "✓ Celery worker running"
    else
        log_error "✗ Celery worker NOT running"
    fi

    if pgrep -f "celery.*beat" > /dev/null; then
        log_success "✓ Celery beat running"
    else
        log_error "✗ Celery beat NOT running"
    fi

    echo ""

    # Check database
    if command -v psql &> /dev/null; then
        if psql -U postgres -d cloud_ai_trading -c "SELECT 1" > /dev/null 2>&1; then
            log_success "✓ Database connected"
        else
            log_error "✗ Database NOT connected"
        fi
    fi

    # Check Redis
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping > /dev/null 2>&1; then
            log_success "✓ Redis connected"
        else
            log_error "✗ Redis NOT connected"
        fi
    fi
}

test_api() {
    log_info "Testing API endpoints..."

    # Check if backend is running
    if ! pgrep -f "uvicorn.*app.main:app" > /dev/null; then
        log_error "Backend is not running. Start services first with: ./deploy.sh start"
        exit 1
    fi

    log_info "Waiting for backend to be ready..."
    sleep 3

    # Try to get a token (assuming test user exists)
    log_info "Testing health endpoint..."
    HEALTH=$(curl -s http://localhost:8000/api/health)

    if echo "$HEALTH" | grep -q "healthy"; then
        log_success "✓ Health endpoint working"
    else
        log_warning "Health check returned: $HEALTH"
    fi

    # Test system endpoints
    log_info "Testing system endpoints..."

    # These require authentication, so they may return 401 without token
    METRICS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/api/system/metrics)
    if [ "$METRICS" = "401" ] || [ "$METRICS" = "200" ]; then
        log_success "✓ Metrics endpoint available (HTTP $METRICS)"
    else
        log_error "✗ Metrics endpoint error (HTTP $METRICS)"
    fi

    LOGS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/api/system/logs)
    if [ "$LOGS" = "401" ] || [ "$LOGS" = "200" ]; then
        log_success "✓ Logs endpoint available (HTTP $LOGS)"
    else
        log_error "✗ Logs endpoint error (HTTP $LOGS)"
    fi

    TASKS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/api/system/tasks)
    if [ "$TASKS" = "401" ] || [ "$TASKS" = "200" ]; then
        log_success "✓ Tasks endpoint available (HTTP $TASKS)"
    else
        log_error "✗ Tasks endpoint error (HTTP $TASKS)"
    fi
}

show_logs() {
    log_info "Tailing service logs..."
    echo ""
    echo "Backend logs:"
    if [ -f "$BACKEND_DIR/backend.log" ]; then
        tail -20 "$BACKEND_DIR/backend.log"
    else
        log_warning "backend.log not found"
    fi

    echo ""
    echo "Celery worker logs:"
    if [ -f "$BACKEND_DIR/celery_worker.log" ]; then
        tail -20 "$BACKEND_DIR/celery_worker.log"
    else
        log_warning "celery_worker.log not found"
    fi

    echo ""
    echo "Celery beat logs:"
    if [ -f "$BACKEND_DIR/celery_beat.log" ]; then
        tail -20 "$BACKEND_DIR/celery_beat.log"
    else
        log_warning "celery_beat.log not found"
    fi
}

# Main
main() {
    ACTION=${1:-all}

    case "$ACTION" in
        all)
            check_requirements
            install_dependencies
            run_migration
            start_services
            check_status
            ;;
        install)
            check_requirements
            install_dependencies
            ;;
        migrate)
            check_requirements
            check_venv
            run_migration
            ;;
        test-api)
            test_api
            ;;
        start)
            check_venv
            start_services
            check_status
            ;;
        stop)
            stop_services
            ;;
        status)
            check_status
            ;;
        logs)
            show_logs
            ;;
        *)
            echo "Usage: $0 {all|install|migrate|test-api|start|stop|status|logs}"
            exit 1
            ;;
    esac
}

# Run main
main "$@"
