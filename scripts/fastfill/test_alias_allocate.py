"""Concurrent + sequential allocate_random_run_email hardening checks.

Asserts:
  * two rapid (multiprocess) allocates mint different emails
  * both issued emails persist in alias_state used_emails (no lock race drop)
  * sequential load_next_alias_index / save_next_alias_index stay dead
  * prepare_dummy_run form values[EMAIL] matches identity.email
"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from field_map import (  # noqa: E402
    ALIAS_STATE_FILE,
    EMAIL,
    allocate_random_run_email,
    load_next_alias_index,
    save_next_alias_index,
)
from run_identity import assert_non_sequential_run_email, prepare_dummy_run  # noqa: E402


def _worker_allocate(q: mp.Queue) -> None:
    try:
        alloc = allocate_random_run_email()
        q.put({"ok": True, "email": alloc["email"], "token": alloc["alias_token"]})
    except Exception as exc:  # noqa: BLE001 — surface to parent
        q.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def test_two_rapid_allocates_differ(n: int = 8) -> dict:
    """Spawn ``n`` processes that allocate nearly simultaneously (default 8)."""
    ctx = mp.get_context("spawn")
    q: mp.Queue = ctx.Queue()
    procs = [ctx.Process(target=_worker_allocate, args=(q,)) for _ in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        if p.is_alive():
            p.terminate()
            p.join(timeout=5)
            raise RuntimeError("allocate worker hung")

    results = [q.get(timeout=5) for _ in range(n)]
    for r in results:
        if not r.get("ok"):
            raise RuntimeError(f"allocate worker failed: {r}")
        assert_non_sequential_run_email(r["email"], r["token"])

    emails = [r["email"] for r in results]
    if len(emails) != len(set(e.lower() for e in emails)):
        raise AssertionError(f"rapid allocates collided: {emails}")

    state = json.loads(ALIAS_STATE_FILE.read_text())
    used = {str(x).lower() for x in (state.get("used_emails") or []) if isinstance(x, str)}
    missing = [em for em in emails if em.lower() not in used]
    if missing:
        raise AssertionError(
            f"issued emails missing from used_emails after concurrent allocate: {missing}"
        )

    return {
        "n": n,
        "emails": emails,
        "emails_differ": True,
        "all_unique": True,
        "all_persisted": True,
    }


def test_sequential_api_dead() -> None:
    try:
        load_next_alias_index("adhoc")
    except RuntimeError:
        pass
    else:
        raise AssertionError("load_next_alias_index must raise")
    try:
        save_next_alias_index("adhoc", 1)
    except RuntimeError:
        pass
    else:
        raise AssertionError("save_next_alias_index must raise")


def test_form_email_matches_identity() -> dict:
    """Logical prepare (no tectonic): values[EMAIL] == identity.email."""
    a = prepare_dummy_run(compile_pdf=False)
    b = prepare_dummy_run(compile_pdf=False)
    assert_non_sequential_run_email(a.email, a.alias_token)
    assert_non_sequential_run_email(b.email, b.alias_token)
    if a.email == b.email:
        raise AssertionError(f"sequential prepares reused email: {a.email}")
    if a.values.get(EMAIL) != a.email:
        raise AssertionError(
            f"form≠identity email: values={a.values.get(EMAIL)!r} identity={a.email!r}"
        )
    if b.values.get(EMAIL) != b.email:
        raise AssertionError(
            f"form≠identity email: values={b.values.get(EMAIL)!r} identity={b.email!r}"
        )
    return {
        "run_a": a.email,
        "run_b": b.email,
        "emails_differ": True,
        "form_matches_identity": True,
    }


def main() -> int:
    out: dict = {"ok": False}
    try:
        test_sequential_api_dead()
        out["sequential_api_dead"] = True
        out["rapid"] = test_two_rapid_allocates_differ()
        out["prepare"] = test_form_email_matches_identity()
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(out, indent=2))
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
