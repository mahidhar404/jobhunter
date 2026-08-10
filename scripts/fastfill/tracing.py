#!/usr/bin/env python3
"""Phase 5 — LLM tracing with a local JSONL mirror and optional Langfuse.

Every traced call is PII-masked, then written to a local JSONL
(``learning_store/llm_traces.jsonl``). When ``LANGFUSE_*`` env is configured and
the SDK imports, the same masked payload is ALSO sent to Langfuse. Langfuse is
therefore purely additive: if it is absent, disabled, or errors, the JSONL trace
still lands — a trace path never breaks a fill.

Enabled only when ``FASTFILL_TRACE=1`` (default off) so tests / normal runs write
nothing unless observability is explicitly requested. Dummy-only: masking runs
regardless, so even a mistaken real-mode call cannot persist cleartext PII.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STORE_DIR = HERE / "learning_store"
TRACES_PATH = STORE_DIR / "llm_traces.jsonl"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\s\-.]?){9,}\d(?!\d)")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def tracing_enabled() -> bool:
    # Default ON: every LLM call writes a PII-masked local JSONL trace (and
    # mirrors to Langfuse only if configured). Set FASTFILL_TRACE=0 to disable.
    return os.environ.get("FASTFILL_TRACE", "1") != "0"


def mask_pii(text: Any) -> Any:
    """Redact emails / phones / SSNs from a string (recurses dict/list values)."""
    if isinstance(text, dict):
        return {k: mask_pii(v) for k, v in text.items()}
    if isinstance(text, (list, tuple)):
        return [mask_pii(v) for v in text]
    if not isinstance(text, str):
        return text
    out = _EMAIL_RE.sub("{{EMAIL}}", text)
    out = _SSN_RE.sub("{{SSN}}", out)
    out = _PHONE_RE.sub("{{PHONE}}", out)
    return out


def _langfuse_client():
    """Return a Langfuse client if configured + importable, else None."""
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse import Langfuse

        return Langfuse()
    except Exception:
        return None


def trace_llm(
    name: str,
    *,
    prompt: Any = None,
    response: Any = None,
    model: str | None = None,
    metadata: dict | None = None,
    traces_path: Path | str | None = None,
) -> dict | None:
    """Record one LLM call. Returns the masked row written (or None if disabled).

    Always safe: masks first, writes JSONL, then best-effort mirrors to Langfuse.
    """
    if not tracing_enabled():
        return None
    row = {
        "ts": round(time.time(), 3),
        "name": name,
        "model": model,
        "prompt": mask_pii(prompt),
        "response": mask_pii(response),
        "metadata": mask_pii(metadata or {}),
    }
    tp = Path(traces_path) if traces_path else TRACES_PATH
    try:
        tp.parent.mkdir(parents=True, exist_ok=True)
        with tp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        pass
    client = _langfuse_client()
    if client is not None:
        try:
            client.trace(
                name=name,
                input=row["prompt"],
                output=row["response"],
                metadata={**row["metadata"], "model": model},
            )
        except Exception:
            pass
    return row


if __name__ == "__main__":
    # tiny self-check
    assert mask_pii("reach me at a@b.com or 405-555-0100") == "reach me at {{EMAIL}} or {{PHONE}}"
    print("tracing self-test OK")
