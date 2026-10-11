"""AP-4: the dashboard's Pydantic models and their TypeScript mirror agree.

Every model ``X`` in ``jarvis/trading_dashboard/schema.py`` has an interface
``TradingX`` in ``frontend/src/types/trading.ts`` with exactly the same field
names; a field that may be None in Python is ``| null`` in TypeScript.
"""

from __future__ import annotations

import json
import re
import types
import typing
from pathlib import Path

from pydantic import BaseModel

from jarvis.trading.paper_spec import DEFAULT_SPEC
from jarvis.trading_dashboard import schema, views
from jarvis.trading_dashboard.reasons import CODES

_TS = Path(__file__).resolve().parents[3] / "jarvis/ui/web/frontend/src/types/trading.ts"
_INTERFACE = re.compile(r"export interface Trading(\w+) \{\n(.*?)\n\}", re.S)
_FIELD = re.compile(r"^  (\w+)(\?)?: (.+);$", re.M)


def _interfaces() -> dict[str, dict[str, str]]:
    text = _TS.read_text(encoding="utf-8")
    return {
        name: {m.group(1): m.group(3) for m in _FIELD.finditer(body)}
        for name, body in _INTERFACE.findall(text)
    }


def _models() -> dict[str, type[BaseModel]]:
    return {
        name: obj
        for name in schema.__all__
        if isinstance(obj := getattr(schema, name), type) and issubclass(obj, BaseModel)
    }


def _nullable(annotation: object) -> bool:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return type(None) in typing.get_args(annotation)
    return False


def test_every_model_has_an_interface_with_the_same_fields() -> None:
    interfaces = _interfaces()
    models = _models()
    assert set(models) <= set(interfaces), set(models) - set(interfaces)
    for name, model in models.items():
        assert set(model.model_fields) == set(interfaces[name]), name


def test_optional_fields_are_nullable_in_typescript() -> None:
    interfaces = _interfaces()
    for name, model in _models().items():
        for field, info in model.model_fields.items():
            ts = interfaces[name][field]
            assert _nullable(info.annotation) == ts.endswith("| null"), f"{name}.{field}"


_LOCALES = _TS.parents[1] / "i18n" / "locales" / "trading"


def _keys(node: object, prefix: str = "") -> set[str]:
    if isinstance(node, dict):
        return {k for key, value in node.items() for k in _keys(value, f"{prefix}{key}.")}
    return {prefix.rstrip(".")}


def test_every_backend_code_has_a_label_in_both_languages() -> None:
    needed = {f"reason.{c}" for c in (*CODES, "approved")}
    needed |= {f"decisions.kind.{k}" for kinds in views.GROUPS.values() for k in kinds}
    needed |= {f"decisions.group.{g}" for g in ("all", *views.GROUPS)}
    needed |= {
        f"warning.{w}"
        for w in (
            "spec_mismatch",
            "run_overdue",
            "runs_missed",
            "data_problem",
            "kill_switch",
            "rows_truncated",
            "bad_rows",
            "job_refused",
        )
    }
    needed |= {
        f"test.state.{s}" for s in ("not_enabled", "running", "finished", "disabled", "refused")
    }
    needed |= {f"source.{s}" for s in ("missing", "busy", "invalid", "error")}
    needed |= {
        f"positions.limit.{k}"
        for k in ("open_risk", "gross_exposure", "positions", "daily_loss", "drawdown")
    }
    needed |= {
        f"health.incident_kind.{k}"
        for k in (
            "job_refused",
            "job_expired",
            "job_disabled",
            "risk_event",
            "execution_failed",
            "cancelled",
            "no_signal",
        )
    }
    needed |= {f"strategy.{s.strategy}" for s in DEFAULT_SPEC.streams}
    for lang in ("de", "en"):
        keys = _keys(json.loads((_LOCALES / f"{lang}.json").read_text(encoding="utf-8")))
        assert needed <= keys, (lang, sorted(needed - keys))


def test_every_live_code_has_a_label_in_both_languages() -> None:
    from jarvis.live_history.archive import RunStatus
    from jarvis.live_history.csv_import import ImportKind, IssueCode
    from jarvis.live_history.reconcile import Code, PnlBasis, Severity
    from jarvis.live_history.sync import Stream
    from jarvis.live_history.venue_rest import ErrorKind

    needed = {f"live.recon.code.{c.value}" for c in Code}
    needed |= {f"live.recon.severity.{s.value}" for s in Severity}
    needed |= {f"live.recon.basis_name.{b.value}" for b in PnlBasis} | {
        "live.recon.basis_name.mixed"
    }
    needed |= {f"live.recon.group.{g}" for g in ("internal", "journal", "balance")}
    needed |= {f"live.issue.{c.value}" for c in IssueCode}
    needed |= {f"live.sources.import_kind.{k.value}" for k in ImportKind}
    needed |= {f"live.sources.error.{e.value}" for e in ErrorKind}
    needed |= {f"live.sources.status.{r.value}" for r in RunStatus}
    needed |= {f"live.sources.kind.{k}" for k in ("sync", "backfill", "import")}
    needed |= {f"live.sources.stream.{s.value}" for s in Stream}
    needed |= {f"live.sources.gap.{r}" for r in ("api", "export")}
    needed |= {f"live.sources.note.{n}" for n in ("retention_unknown", "beyond_retention")}
    needed |= {
        f"live.sources.entity.{e}"
        for e in ("position", "fill", "order", "balance", "journal_entry")
    }
    needed |= {
        f"live.warning.{w}"
        for w in (
            "no_sync_yet",
            "sync_old",
            "last_run_failed",
            "last_run_partial",
            "coverage_gaps",
            "import_issues",
            "records_truncated",
        )
    }
    needed |= {f"live.source.{s}" for s in ("missing", "busy", "invalid", "error")}
    needed |= {f"live.origin.{o}" for o in ("api", "csv")}
    needed |= {f"live.role.{r}" for r in ("maker", "taker", "unknown")}
    needed |= {f"live.fill_side.{s}" for s in ("buy", "sell")}
    needed |= {f"mode.{m}" for m in ("paper", "live")}
    for lang in ("de", "en"):
        keys = _keys(json.loads((_LOCALES / f"{lang}.json").read_text(encoding="utf-8")))
        assert needed <= keys, (lang, sorted(needed - keys))
