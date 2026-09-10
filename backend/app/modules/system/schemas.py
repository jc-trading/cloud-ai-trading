"""
Pydantic schemas for system monitoring endpoints.
"""

from typing import Optional

from pydantic import BaseModel


class BarStoreResponse(BaseModel):
    """Size of the Parquet bar store."""

    path: str
    bytes: int
    files: int
    human: Optional[str] = None
