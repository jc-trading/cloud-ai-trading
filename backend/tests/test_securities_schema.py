"""P2: the asset-type vocabulary is written in three places — the ORM check,
the classifier, and migration 020's literal. They must not drift apart."""

import re
from pathlib import Path

import pytest

from quant.data import securities

from app.modules.market.models import SECURITY_ASSET_TYPES

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = "020_securities.py"


def _migration_path() -> Path:
    for candidate in (BACKEND / "migrations" / "versions" / MIGRATION,
                      BACKEND.parent / "runtime" / "p2" / MIGRATION,
                      BACKEND / "runtime" / "p2" / MIGRATION):
        if candidate.exists():
            return candidate
    pytest.fail(f"{MIGRATION} not found in migrations/versions or runtime/p2")


def test_asset_types_agree_everywhere():
    text = _migration_path().read_text()
    literal = re.search(r'"asset_type IN \((.*?)\)"', text, re.S)
    assert literal, "ck_security_asset_type literal not found in migration 020"
    migration_types = tuple(re.findall(r"'([a-z]+)'", literal.group(1)))
    assert SECURITY_ASSET_TYPES == securities.ASSET_TYPES == migration_types
    assert len(migration_types) == 11
