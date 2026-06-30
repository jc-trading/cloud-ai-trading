"""
Middleware: CORS, request logging, rate limiting.
"""

import time
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import get_settings
from app.modules.system.logging_middleware import SystemLogMiddleware

settings = get_settings()
logger = logging.getLogger("cloud_ai_trading")

limiter = Limiter(key_func=get_remote_address)


def setup_middleware(app: FastAPI) -> None:
    """Configure all middleware for the FastAPI application."""

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limit error handler
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Persist request/response events for the System Monitoring log view.
    app.add_middleware(SystemLogMiddleware)

    # Request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.info(
            f"{request.method} {request.url.path} "
            f"- Status: {response.status_code} "
            f"- Time: {process_time:.3f}s"
        )
        return response
