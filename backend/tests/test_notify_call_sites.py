"""Every business-event ``_notify`` call names a known event and severity (P4).

Parsed from source, so a new call site without routing metadata — or with a
typo that would silently fall back to the legacy Telegram path — fails here.
The seeded rule list in migration 021 must cover exactly ``EVENT_TYPES``.
"""

import ast
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.notifications.models import EVENT_TYPES, SEVERITIES

_BACKEND = Path(__file__).resolve().parents[1]
_SOURCES = (_BACKEND / "app/tasks/quant_tasks.py", _BACKEND / "tasks/celery_app.py")
# quant_tasks 20 + celery_app.check_master_settings 1
_EXPECTED_SITES = 21


def _notify_calls():
    for path in _SOURCES:
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_notify"):
                yield path.name, node


def test_every_notify_call_carries_constant_event_and_severity():
    calls = list(_notify_calls())
    assert len(calls) == _EXPECTED_SITES
    for name, call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        where = f"{name}:{call.lineno}"
        assert set(kw) == {"event", "severity"}, where
        assert isinstance(kw["event"], ast.Constant), where
        assert isinstance(kw["severity"], ast.Constant), where
        assert kw["event"].value in EVENT_TYPES, where
        assert kw["severity"].value in SEVERITIES, where


def test_migration_seeds_a_rule_for_every_event_type():
    candidates = (sorted((_BACKEND / "migrations/versions").glob("021_*.py"))
                  or sorted((_BACKEND.parent / "runtime/p4_stage").glob("021_*.py")))
    assert candidates, "migration 021 not found in migrations/versions or runtime/p4_stage"
    tree = ast.parse(candidates[0].read_text())
    seeded = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                  if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", None) == "_EVENT_TYPES" for t in n.targets))
    assert list(seeded) == list(EVENT_TYPES)
