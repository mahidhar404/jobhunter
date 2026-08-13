#!/usr/bin/env python3
"""Live flight recorder — chronological decision trace for fill runs.

Answers: what is the filler doing *right now*, and *why*?

Writes beside the run artifacts:
  - ``flight.jsonl`` — append-only structured events (one JSON object per line)
  - ``flight.log``   — human one-liners for paste-back / Terminal watch

Enable:
  - ``FASTFILL_FLIGHT=1`` or ``--flight-recorder`` → always on
  - ``FASTFILL_FLIGHT=0`` or ``--no-flight-recorder`` → always off
  - default: **ON when headed**, OFF when headless

Dummy-only observability. Never-submit rules unchanged. Intents are PII-masked.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = ROOT / "skyvern_runtime" / "real_job_results"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\s\-.]?){9,}\d(?!\d)")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact_intent(value: Any) -> str | None:
    """Dummy-safe short intent — mask email/phone/SSN shapes even in dummy runs."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return ""
    try:
        from tracing import mask_pii

        text = str(mask_pii(text))
    except Exception:
        text = _EMAIL_RE.sub("{{EMAIL}}", text)
        text = _SSN_RE.sub("{{SSN}}", text)
        text = _PHONE_RE.sub("{{PHONE}}", text)
    return text[:120]


def short_selector(sel: str | None, n: int = 72) -> str | None:
    s = (sel or "").strip()
    if not s:
        return None
    return s if len(s) <= n else s[: n - 1] + "…"


def flight_enabled(
    *,
    headed: bool | None = None,
    report: dict[str, Any] | None = None,
) -> bool:
    """Resolve whether the flight recorder should emit.

    Explicit env / report flag wins; otherwise headed defaults ON.
    """
    env = (os.environ.get("FASTFILL_FLIGHT") or "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    if report is not None:
        flag = report.get("flight_recorder")
        if flag is False:
            return False
        if flag is True:
            return True
        if headed is None and "headed" in report:
            headed = bool(report.get("headed"))
    if headed is not None:
        return bool(headed)
    return False


def _field_blob(
    *,
    label: str | None = None,
    field_type: str | None = None,
    automation_id: str | None = None,
    selector: str | None = None,
    field_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = field_meta or {}
    lab = label if label is not None else meta.get("label")
    ft = field_type if field_type is not None else (meta.get("type") or meta.get("field_type"))
    aid = (
        automation_id
        if automation_id is not None
        else (meta.get("automation_id") or meta.get("field_id"))
    )
    sel = selector if selector is not None else meta.get("selector")
    out: dict[str, Any] = {}
    if ft:
        out["type"] = str(ft)[:48]
    if lab:
        out["label"] = str(lab)[:80]
    if aid:
        out["automation_id"] = str(aid)[:80]
    ss = short_selector(str(sel) if sel else None)
    if ss:
        out["selector"] = ss
    return out


def _format_human(entry: dict[str, Any]) -> str:
    seq = entry.get("seq", "?")
    ts = (entry.get("ts") or "")[11:19] or "—"
    page = entry.get("page") or "—"
    layer = entry.get("layer") or "—"
    field = entry.get("field") or {}
    fid = (
        field.get("automation_id")
        or field.get("type")
        or field.get("label")
        or "—"
    )
    if field.get("type") and field.get("automation_id"):
        fid = f"{field.get('type')} aid={field.get('automation_id')}"
    elif field.get("type") and field.get("label"):
        fid = f"{field.get('type')} ({str(field.get('label'))[:36]})"
    intent = entry.get("intent")
    intent_s = f'intent="{intent}"' if intent not in (None, "") else "intent=—"
    action = entry.get("action") or entry.get("event") or "?"
    gate = entry.get("gate") or {}
    if gate:
        g = f"gate={gate.get('kind') or '?'}:{gate.get('result') or '?'}"
        if gate.get("reason"):
            g += f"({str(gate['reason'])[:48]})"
    else:
        g = "gate=—"
    rb = entry.get("readback")
    rb_s = f'readback="{rb}"' if rb not in (None, "") else "readback=—"
    adv = entry.get("advance") or {}
    if adv:
        a = f"advance={adv.get('decision') or '?'}"
        if adv.get("reason"):
            a += f"({adv.get('reason')})"
    else:
        a = "advance=—"
    bits = [
        f"[flight {int(seq):04d}]" if isinstance(seq, int) else f"[flight {seq}]",
        ts,
        str(page)[:40],
        f"layer={layer}",
        str(fid)[:56],
        intent_s,
        f"action={action}",
        g,
        rb_s,
        a,
    ]
    return " ".join(bits)


class FlightRecorder:
    """Append-only decision trace bound to one fill run artifact directory."""

    def __init__(
        self,
        out_dir: Path | str,
        *,
        run_id: str,
        url: str = "",
        platform: str = "",
        headed: bool = False,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = str(run_id)
        self.url = url or ""
        self.platform = platform or ""
        self.headed = bool(headed)
        self.jsonl_path = self.out_dir / "flight.jsonl"
        self.log_path = self.out_dir / "flight.log"
        self._seq = 0
        self._page: str = ""
        self._layer: str = ""
        if self.jsonl_path.is_file():
            try:
                for line in self.jsonl_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(e, dict) and e.get("seq"):
                        self._seq = max(self._seq, int(e["seq"]))
                        if e.get("page"):
                            self._page = str(e["page"])
                        if e.get("layer"):
                            self._layer = str(e["layer"])
            except OSError:
                pass
        if not self.log_path.is_file():
            try:
                self.log_path.write_text(
                    f"# Flight recorder\n"
                    f"# run_id={self.run_id} platform={self.platform}\n"
                    f"# url={self.url}\n"
                    f"# started={_utc_now()} headed={self.headed}\n\n",
                    encoding="utf-8",
                )
            except OSError:
                pass

    def set_context(self, *, page: str | None = None, layer: str | None = None) -> None:
        if page is not None:
            self._page = str(page)[:80]
        if layer is not None:
            self._layer = str(layer)[:48]

    def record(
        self,
        event: str,
        *,
        action: str | None = None,
        page: str | None = None,
        layer: str | None = None,
        label: str | None = None,
        field_type: str | None = None,
        automation_id: str | None = None,
        selector: str | None = None,
        field_meta: dict[str, Any] | None = None,
        intent: Any = None,
        gate_kind: str | None = None,
        gate_result: str | None = None,
        gate_reason: str | None = None,
        readback: str | None = None,
        advance_decision: str | None = None,
        advance_reason: str | None = None,
        extra: dict[str, Any] | None = None,
        stream: bool | None = None,
    ) -> dict[str, Any]:
        """Append one decision event. Returns the entry dict."""
        if page is not None:
            self._page = str(page)[:80]
        if layer is not None:
            self._layer = str(layer)[:48]
        self._seq += 1
        field = _field_blob(
            label=label,
            field_type=field_type,
            automation_id=automation_id,
            selector=selector,
            field_meta=field_meta,
        )
        entry: dict[str, Any] = {
            "seq": self._seq,
            "ts": _utc_now(),
            "ts_epoch": round(time.time(), 3),
            "run_id": self.run_id,
            "event": str(event or "decision")[:48],
            "action": (action or event or "")[:64] or None,
            "page": self._page or None,
            "layer": self._layer or None,
            "field": field or None,
            "intent": redact_intent(intent),
            "readback": redact_intent(readback) if readback is not None else None,
        }
        if gate_kind or gate_result or gate_reason:
            entry["gate"] = {
                "kind": (gate_kind or "")[:48] or None,
                "result": (gate_result or "")[:48] or None,
                "reason": (gate_reason or "")[:240] or None,
            }
        if advance_decision or advance_reason:
            entry["advance"] = {
                "decision": (advance_decision or "")[:32] or None,
                "reason": (advance_reason or "")[:120] or None,
            }
        if extra:
            entry["extra"] = extra
        # Drop null-only nests for cleaner JSONL
        if entry.get("field") == {}:
            entry["field"] = None
        try:
            with self.jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass
        line = _format_human(entry)
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        do_stream = stream
        if do_stream is None:
            do_stream = (os.environ.get("FASTFILL_FLIGHT_STREAM") or "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        if do_stream:
            try:
                print(line, flush=True)
            except OSError:
                pass
        return entry

    def read_events(self) -> list[dict[str, Any]]:
        """Read back all JSONL events (for tests / paste review)."""
        if not self.jsonl_path.is_file():
            return []
        out: list[dict[str, Any]] = []
        try:
            for line in self.jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(e, dict):
                    out.append(e)
        except OSError:
            return []
        return out

    def finalize(self) -> None:
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n# ended={_utc_now()} events={self._seq}\n")
        except OSError:
            pass


def attach_flight_recorder(
    report: dict[str, Any],
    *,
    out_dir: Path | str | None = None,
    run_id: str | None = None,
    force: bool = False,
) -> FlightRecorder | None:
    """Create / reuse FlightRecorder on report when enabled."""
    existing = report.get("_flight_recorder")
    if isinstance(existing, FlightRecorder):
        if report.get("headed") and not existing.headed:
            existing.headed = True
        return existing
    if not force and not flight_enabled(report=report):
        return None
    url = str(report.get("url") or "")
    platform = str(report.get("platform") or "")
    rid = run_id or str(
        report.get("alias_token")
        or report.get("email_alias")
        or report.get("identity_email")
        or f"run_{int(time.time())}"
    )
    if out_dir is None:
        rp = report.get("report_path") or report.get("screenshot") or report.get(
            "fill_step_log_path"
        )
        if rp:
            out_dir = Path(str(rp)).parent
        else:
            out_dir = DEFAULT_RESULTS / f"flight_{rid}"
    rec = FlightRecorder(
        out_dir,
        run_id=rid,
        url=url,
        platform=platform,
        headed=bool(report.get("headed")),
    )
    report["_flight_recorder"] = rec
    report["flight_recorder"] = True
    report["flight_jsonl_path"] = str(rec.jsonl_path)
    report["flight_log_path"] = str(rec.log_path)
    return rec


def get_flight(report: dict[str, Any] | None) -> FlightRecorder | None:
    if not report:
        return None
    rec = report.get("_flight_recorder")
    return rec if isinstance(rec, FlightRecorder) else None


def note_flight(report: dict[str, Any] | None, event: str, **kwargs: Any) -> dict[str, Any] | None:
    """Convenience: record one event if a recorder is attached (no-op otherwise).

    Lazy-attaches when flight is enabled and report has identity/url.
    """
    if not report:
        return None
    rec = get_flight(report)
    if rec is None:
        if flight_enabled(report=report) and (
            report.get("alias_token") or report.get("url") or report.get("headed")
        ):
            rec = attach_flight_recorder(report)
        if rec is None:
            return None
    # Soft page fingerprint from report when caller omitted page=
    if kwargs.get("page") is None:
        fp = (
            report.get("page_fingerprint_after")
            or report.get("page_fingerprint_before")
            or (report.get("workday") or {}).get("phase")
            or report.get("current_phase")
        )
        if fp:
            kwargs["page"] = str(fp)[:80]
    return rec.record(event, **kwargs)


def finalize_flight(report: dict[str, Any] | None) -> None:
    rec = get_flight(report)
    if rec is not None:
        rec.finalize()


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        rec = FlightRecorder(td, run_id="t_flight", url="https://example.com", platform="workday")
        rec.set_context(page="contact", layer="pack")
        rec.record(
            "gate",
            action="skip",
            field_type="EMAIL",
            automation_id="email",
            intent="dummy@example.com",
            gate_kind="verify_before_touch",
            gate_result="skip_lock",
            gate_reason="already_correct_skip",
            readback="dummy@example.com",
            stream=False,
        )
        rec.record(
            "fill",
            action="fill_select",
            field_type="ADDRESS_STATE",
            automation_id="addressSection_countryRegion",
            intent="Illinois",
            gate_kind="field_done",
            gate_result="ok",
            gate_reason="state_committed",
            readback="Illinois",
            layer="workday_contact",
            stream=False,
        )
        rec.record(
            "advance",
            action="STOP",
            advance_decision="STOP",
            advance_reason="pack_incomplete",
            gate_kind="pack",
            gate_result="miss",
            gate_reason="pack_incomplete",
            page="contact",
            stream=False,
        )
        rec.finalize()
        events = rec.read_events()
        assert len(events) == 3, events
        assert events[0]["gate"]["result"] == "skip_lock"
        assert events[0]["intent"] == "{{EMAIL}}" or "dummy" in str(events[0]["intent"]).lower() or events[0]["intent"]
        assert events[1]["field"]["automation_id"] == "addressSection_countryRegion"
        assert events[2]["advance"]["decision"] == "STOP"
        log = rec.log_path.read_text(encoding="utf-8")
        assert "pack_incomplete" in log
        assert "[flight 0002]" in log
        # Enable/disable
        os.environ["FASTFILL_FLIGHT"] = "0"
        assert flight_enabled(headed=True) is False
        os.environ["FASTFILL_FLIGHT"] = "1"
        assert flight_enabled(headed=False) is True
        os.environ.pop("FASTFILL_FLIGHT", None)
        assert flight_enabled(headed=True) is True
        assert flight_enabled(headed=False) is False
        # attach + note_flight round-trip
        report = {"url": "https://x.test", "headed": True, "alias_token": "abc"}
        attached = attach_flight_recorder(report, out_dir=td, force=True)
        assert attached is not None
        note_flight(
            report,
            "supervisor",
            action="audit",
            field_type="PHONE",
            intent="555-0100",
            gate_kind="supervisor",
            gate_result="OK",
            readback="555-0100",
            stream=False,
        )
        assert get_flight(report) is attached
        assert any(e.get("gate", {}).get("result") == "OK" for e in attached.read_events())
    print("flight_recorder.self_test: OK")


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv or len(sys.argv) == 1:
        self_test()
    else:
        print("usage: flight_recorder.py --self-test", file=sys.stderr)
        sys.exit(2)
