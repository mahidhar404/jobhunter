#!/usr/bin/env python3
"""Phase 7 (optional/later) — DSPy prompt-optimization hook.

Scaffold for optimizing the leftover-answer prompt against the eval metrics
(``metrics_timeline`` pass rate) using DSPy. It is intentionally inert until
``dspy`` is installed AND ``FASTFILL_DSPY=1`` — a missing dep or unset flag makes
this a no-op that reports "not configured", so nothing here can affect a fill.

When fully configured and a trainset of ``{question, answer}`` leftover pairs is
supplied, ``optimize()`` runs a BootstrapFewShot pass and writes the compiled
prompt artifact under ``learning_store/dspy_leftover_prompt.txt`` for optional
manual review — it does **not** auto-swap the live ``flash_leftovers`` system
prompt (that stays a human promote step).

CLI::

    python dspy_optimize.py --status
    FASTFILL_DSPY=1 python dspy_optimize.py --optimize   # requires dspy + data
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
STORE_DIR = HERE / "learning_store"
ARTIFACT = STORE_DIR / "dspy_leftover_prompt.txt"


def enabled() -> bool:
    return os.environ.get("FASTFILL_DSPY", "0") == "1"


def _dspy_available() -> bool:
    try:
        import dspy  # noqa: F401

        return True
    except Exception:
        return False


def coverage_metric(example, prediction, *_a, **_k) -> float:
    """DSPy-shaped metric: 1.0 when the predicted answer is non-empty + matches
    the expected shape, else 0.0. (Coverage-first: never leave a field empty.)"""
    got = getattr(prediction, "answer", None) or getattr(prediction, "value", None) or ""
    want_nonempty = bool(str(got).strip())
    expected = getattr(example, "answer", None)
    if expected is None:
        return 1.0 if want_nonempty else 0.0
    return 1.0 if want_nonempty and str(got).strip() == str(expected).strip() else 0.0


def status() -> dict:
    return {
        "flag_enabled": enabled(),
        "dspy_installed": _dspy_available(),
        "ready": enabled() and _dspy_available(),
        "artifact": str(ARTIFACT) if ARTIFACT.is_file() else None,
    }


def _load_trainset(trainset):
    """Normalize trainset into list[{question, answer}]."""
    if not trainset:
        # Best-effort: pull a few sanitized leftover Q/A pairs from experience.
        try:
            from continuous_learn import load_experience

            rows = []
            for row in reversed(load_experience()):
                if not row.get("ok") and not row.get("verified"):
                    continue
                lab = str(row.get("label") or "").strip()
                val = str(row.get("value") or "").strip()
                if not lab or not val or val.startswith("{{"):
                    continue
                rows.append({"question": lab, "answer": val})
                if len(rows) >= 12:
                    break
            return rows
        except Exception:
            return []
    out = []
    for ex in trainset:
        if isinstance(ex, dict):
            q = ex.get("question") or ex.get("label") or ""
            a = ex.get("answer") or ex.get("value") or ""
            if q and a:
                out.append({"question": str(q), "answer": str(a)})
    return out


def optimize(trainset=None) -> dict:
    """Run DSPy BootstrapFewShot if fully configured; otherwise a documented no-op."""
    st = status()
    if not st["ready"]:
        return {
            "ran": False,
            "reason": "dspy not installed" if not st["dspy_installed"] else "FASTFILL_DSPY!=1",
            **st,
        }
    examples = _load_trainset(trainset)
    if len(examples) < 2:
        return {
            "ran": False,
            "reason": "need >=2 labelled leftover examples",
            "n_examples": len(examples),
            **st,
        }
    try:
        import dspy
        from dspy.teleprompt import BootstrapFewShot
    except Exception as e:  # noqa: BLE001
        return {"ran": False, "reason": f"dspy import failed: {e}", **st}

    class LeftoverFill(dspy.Signature):
        """Fill a leftover job-application field for a FICTIONAL dummy applicant."""

        question = dspy.InputField(desc="field label / question")
        answer = dspy.OutputField(desc="short grounded answer")

    class LeftoverModule(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.Predict(LeftoverFill)

        def forward(self, question):
            return self.predict(question=question)

    dspy_examples = [
        dspy.Example(question=ex["question"], answer=ex["answer"]).with_inputs("question")
        for ex in examples
    ]
    try:
        teleprompter = BootstrapFewShot(metric=coverage_metric, max_bootstrapped_demos=4)
        compiled = teleprompter.compile(LeftoverModule(), trainset=dspy_examples)
    except Exception as e:  # noqa: BLE001 — never break callers
        return {"ran": False, "reason": f"compile failed: {type(e).__name__}: {e}", **st}

    STORE_DIR.mkdir(parents=True, exist_ok=True)
    # Persist a human-readable dump; live prompt swap stays a manual promote.
    try:
        dump = {
            "n_examples": len(examples),
            "demos": [
                {"question": ex["question"], "answer": ex["answer"]} for ex in examples[:8]
            ],
            "note": "Review only — does not auto-replace flash_leftovers system prompt.",
        }
        ARTIFACT.write_text(json.dumps(dump, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"ran": True, "artifact_error": str(e), "compiled": True, **st}
    return {
        "ran": True,
        "n_examples": len(examples),
        "artifact": str(ARTIFACT),
        "compiled": compiled is not None,
        **status(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="DSPy optimization hook (optional)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--optimize", action="store_true")
    args = ap.parse_args()
    if args.optimize:
        print(optimize())
    else:
        print(status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
