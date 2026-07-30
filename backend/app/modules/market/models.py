"""
Market data models (K-line candles).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Numeric, BigInteger, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


