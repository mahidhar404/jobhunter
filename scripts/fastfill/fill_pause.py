"""In-page Pause / Continue overlay for headed fast_fill.

Hard rules:
  - Never submit; never CAPTCHA solve; overlay is review/control only
  - Pause is cooperative: takes effect between fill actions (not mid-widget /
    mid-select / mid-upload). FILL3-009 — UX must not promise near-immediate stop.
  - Continue resumes; callers should rely on already_correct skips so
    human-filled / previously filled values are not thrashed
  - FILL3-002 / FILL3-015 / FILL2-S03: while CAPTCHA wait is active, hide the
    overlay (pointer-events:none, low z-index) so it cannot cover the challenge.
    Remount observer respects the CAPTCHA gate flag.
  - FILL2-S02: Continue fill does NOT clear CAPTCHA wait — Enter / .captcha_continue
  - FILL3-017: throttle overlay re-inject (skip CDP when already mounted recently)
  - Never auto-close the fill browser while Pause is engaged, or after a
    terminal fill when headed ``--hold-open`` (indefinite) is active — only the
    human closes Chrome / ends hold.

Headed default ON. Disable with ``--no-fill-pause`` or
``FASTFILL_FILL_PAUSE=0``.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

# Overlay root id — keep stable for tests / CSS.
OVERLAY_ID = "jh-fill-pause-overlay"
CONTROL_GLOBAL = "__jhFillControl"
CAPTCHA_GATE_GLOBAL = "__jhCaptchaGate"
ACTIVITY_GLOBAL = "__jhFillActivity"

# FILL3-017: skip re-evaluate when overlay was injected this recently (seconds).
_INJECT_THROTTLE_S = 2.0
_last_inject_mono: dict[int, float] = {}
# Last successfully read paused flag per page — CDP errors must not look like Continue.
_last_paused_known: dict[int, bool] = {}

# Mutable fill-activity status (Python side); pushed to window.__jhFillActivity.
_CURRENT_ACTIVITY: dict[str, Any] = {
    "layer": None,
    "layer_label": "idle",
    "action": "idle",
    "label": "",
    "detail": "",
    "updated_at": 0.0,
}

# Human-facing layer names (match fastfill skill / PLAYBOOK).
LAYER_LABELS = {
    "0": "Layer 0 (deterministic/pack)",
    "layer0": "Layer 0 (deterministic/pack)",
    "pack": "Layer 0 (deterministic/pack)",
    "deterministic": "Layer 0 (deterministic/pack)",
    "1": "Layer 1 (extract/verified_select)",
    "layer1": "Layer 1 (extract/verified_select)",
    "extract": "Layer 1 (extract/verified_select)",
    "verified_select": "Layer 1 (extract/verified_select)",
    "2": "Layer 2 (Flash leftovers)",
    "layer2": "Layer 2 (Flash leftovers)",
    "flash": "Layer 2 (Flash leftovers)",
    "leftovers": "Layer 2 (Flash leftovers)",
    "entry": "Entry pre-pass",
    "advance": "Advance / navigation",
    "hold": "Hold for review",
    "captcha": "Waiting CAPTCHA (human)",
    "paused": "Idle (paused)",
    "refill": "In-session refill",
}


def note_fill_activity(
    *,
    layer: str | None = None,
    action: str | None = None,
    label: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """Update in-memory fill activity (hover tooltip source)."""
    if layer is not None:
        key = str(layer).strip().lower()
        _CURRENT_ACTIVITY["layer"] = key or None
        _CURRENT_ACTIVITY["layer_label"] = LAYER_LABELS.get(
            key, str(layer).strip() or "—"
        )
    if action is not None:
        _CURRENT_ACTIVITY["action"] = str(action).strip()[:80] or "idle"
    if label is not None:
        _CURRENT_ACTIVITY["label"] = str(label).strip()[:120]
    if detail is not None:
        _CURRENT_ACTIVITY["detail"] = str(detail).strip()[:160]
    _CURRENT_ACTIVITY["updated_at"] = time.time()
    return dict(_CURRENT_ACTIVITY)


def get_fill_activity() -> dict[str, Any]:
    return dict(_CURRENT_ACTIVITY)


def format_fill_activity_text(act: dict[str, Any] | None = None) -> str:
    """One-line status for the Pause hover popover."""
    a = act if isinstance(act, dict) else _CURRENT_ACTIVITY
    layer = str(a.get("layer_label") or a.get("layer") or "—")
    action = str(a.get("action") or "idle")
    label = str(a.get("label") or "").strip()
    detail = str(a.get("detail") or "").strip()
    parts = [layer, action]
    if label:
        parts.append(label)
    if detail and detail not in (label, action):
        parts.append(detail)
    return " · ".join(parts)[:220]


def should_keep_fill_browser_open(
    *,
    paused: bool = False,
    hold_seconds: int | None = 0,
) -> bool:
    """True when fill must NOT auto-close Chrome (human ends pause/hold).

    - Pause engaged → keep open indefinitely (never race-close behind human).
    - ``hold_seconds < 0`` (``--hold-open`` / HOLD_INDEFINITE) → keep open
      after terminal fill until human closes the window / ends hold.
    Timed positive holds still auto-close after ``_hold_for_review`` returns.
    """
    if paused:
        return True
    try:
        s = int(hold_seconds if hold_seconds is not None else 0)
    except (TypeError, ValueError):
        return False
    return s < 0


def may_auto_close_fill_browser(
    *,
    paused: bool = False,
    hold_seconds: int | None = 0,
) -> bool:
    """Inverse of ``should_keep_fill_browser_open`` — unit-test decision gate."""
    return not should_keep_fill_browser_open(
        paused=paused, hold_seconds=hold_seconds
    )


_OVERLAY_CSS = f"""
#{OVERLAY_ID} {{
  position: fixed !important;
  top: 12px !important;
  right: 12px !important;
  z-index: 2147483646 !important;
  font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif !important;
  pointer-events: auto !important;
}}
#{OVERLAY_ID}.jh-captcha-gated {{
  /* FILL3-002: do not intercept CAPTCHA clicks in the top-right */
  z-index: 1 !important;
  pointer-events: none !important;
  opacity: 0 !important;
  visibility: hidden !important;
}}
#{OVERLAY_ID} button {{
  appearance: none !important;
  border: 1px solid rgba(0,0,0,0.25) !important;
  border-radius: 8px !important;
  padding: 10px 14px !important;
  font-size: 13px !important;
  font-weight: 600 !important;
  letter-spacing: 0.01em !important;
  cursor: pointer !important;
  box-shadow: 0 4px 16px rgba(0,0,0,0.18) !important;
  background: #0f172a !important;
  color: #f8fafc !important;
}}
#{OVERLAY_ID} button.jh-paused {{
  background: #b45309 !important;
  color: #fffbeb !important;
  border-color: #92400e !important;
}}
#{OVERLAY_ID} .jh-hint {{
  margin-top: 6px !important;
  font-size: 11px !important;
  color: #0f172a !important;
  background: rgba(255,255,255,0.92) !important;
  border-radius: 6px !important;
  padding: 4px 8px !important;
  max-width: 220px !important;
  line-height: 1.35 !important;
  box-shadow: 0 2px 8px rgba(0,0,0,0.12) !important;
}}
#{OVERLAY_ID} .jh-activity-tip {{
  display: none !important;
  position: absolute !important;
  top: calc(100% + 6px) !important;
  right: 0 !important;
  min-width: 200px !important;
  max-width: 280px !important;
  padding: 8px 10px !important;
  font-size: 11px !important;
  font-weight: 500 !important;
  line-height: 1.4 !important;
  color: #0f172a !important;
  background: rgba(255,255,255,0.97) !important;
  border: 1px solid rgba(15,23,42,0.18) !important;
  border-radius: 6px !important;
  box-shadow: 0 4px 14px rgba(0,0,0,0.16) !important;
  pointer-events: none !important;
  white-space: normal !important;
  z-index: 1 !important;
}}
#{OVERLAY_ID}.jh-tip-open .jh-activity-tip {{
  display: block !important;
}}
"""

_INSTALL_OVERLAY_JS = f"""
() => {{
  const GID = {CONTROL_GLOBAL!r};
  const OID = {OVERLAY_ID!r};
  const CGATE = {CAPTCHA_GATE_GLOBAL!r};
  const AGID = {ACTIVITY_GLOBAL!r};
  if (!window[GID]) {{
    window[GID] = {{
      paused: false,
      pauseCount: 0,
      continueCount: 0,
      installedAt: Date.now(),
    }};
  }}
  if (!window[AGID]) {{
    window[AGID] = {{
      layer: null,
      layer_label: 'idle',
      action: 'idle',
      label: '',
      detail: '',
      text: 'idle',
      updated_at: 0,
    }};
  }}
  const activityText = () => {{
    const a = window[AGID] || {{}};
    if (a.text) return String(a.text);
    const bits = [a.layer_label || a.layer || '—', a.action || 'idle'];
    if (a.label) bits.push(a.label);
    if (a.detail && a.detail !== a.label) bits.push(a.detail);
    return bits.join(' · ');
  }};
  const applyCaptchaGate = (root) => {{
    if (!root) return;
    if (window[CGATE]) {{
      root.classList.add('jh-captcha-gated');
      root.setAttribute('aria-hidden', 'true');
      root.setAttribute('data-jh-captcha-gated', '1');
      root.classList.remove('jh-tip-open');
    }} else {{
      root.classList.remove('jh-captcha-gated');
      root.removeAttribute('aria-hidden');
      root.removeAttribute('data-jh-captcha-gated');
    }}
  }};
  const ensure = () => {{
    let root = document.getElementById(OID);
    if (root && root.isConnected) {{
      applyCaptchaGate(root);
      return root;
    }}
    root = document.createElement('div');
    root.id = OID;
    root.setAttribute('data-jh-fill-pause', '1');
    const style = document.createElement('style');
    style.textContent = {_OVERLAY_CSS!r};
    root.appendChild(style);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = OID + '-btn';
    btn.setAttribute('aria-label', 'Pause or continue job-hunter autofill');
    const hint = document.createElement('div');
    hint.className = 'jh-hint';
    hint.id = OID + '-hint';
    hint.textContent = 'Pause takes effect between actions (not mid-field).';
    const tip = document.createElement('div');
    tip.className = 'jh-activity-tip';
    tip.id = OID + '-tip';
    tip.setAttribute('role', 'status');
    tip.textContent = activityText();
    let tipTimer = null;
    const refreshTip = () => {{
      tip.textContent = activityText();
    }};
    const openTip = () => {{
      if (window[CGATE]) return;
      refreshTip();
      root.classList.add('jh-tip-open');
      if (tipTimer) clearInterval(tipTimer);
      tipTimer = setInterval(refreshTip, 400);
    }};
    const closeTip = () => {{
      root.classList.remove('jh-tip-open');
      if (tipTimer) {{ clearInterval(tipTimer); tipTimer = null; }}
    }};
    btn.addEventListener('mouseenter', openTip);
    btn.addEventListener('mouseleave', closeTip);
    btn.addEventListener('focus', openTip);
    btn.addEventListener('blur', closeTip);
    const sync = () => {{
      const c = window[GID];
      if (c.paused) {{
        btn.textContent = 'Continue fill';
        btn.classList.add('jh-paused');
        hint.textContent = 'PAUSED between actions — edit fields, then Continue (skips already filled). Does NOT clear CAPTCHA wait.';
      }} else {{
        btn.textContent = 'Pause fill';
        btn.classList.remove('jh-paused');
        hint.textContent = 'Pause takes effect between fill actions (not mid-widget). During CAPTCHA use Enter / .captcha_continue, not Continue fill.';
      }}
      refreshTip();
    }};
    btn.addEventListener('click', (ev) => {{
      ev.preventDefault();
      ev.stopPropagation();
      // FILL3-002: ignore clicks while CAPTCHA gate hides the overlay
      if (window[CGATE]) return;
      const c = window[GID];
      c.paused = !c.paused;
      if (c.paused) c.pauseCount = (c.pauseCount || 0) + 1;
      else c.continueCount = (c.continueCount || 0) + 1;
      sync();
    }}, true);
    root.appendChild(btn);
    root.appendChild(hint);
    root.appendChild(tip);
    sync();
    const mount = () => {{
      const parent = document.body || document.documentElement;
      if (parent && !root.isConnected) parent.appendChild(root);
      applyCaptchaGate(root);
    }};
    mount();
    if (!window.__jhFillPauseObserver) {{
      window.__jhFillPauseObserver = new MutationObserver(() => {{
        // FILL3-002: remount is OK, but always re-apply CAPTCHA gate
        if (!document.getElementById(OID)) mount();
        else applyCaptchaGate(document.getElementById(OID));
      }});
      try {{
        window.__jhFillPauseObserver.observe(document.documentElement, {{
          childList: true,
          subtree: true,
        }});
      }} catch (_) {{}}
    }}
    return root;
  }};
  ensure();
  return {{
    ok: true,
    paused: !!(window[GID] && window[GID].paused),
    pauseCount: (window[GID] && window[GID].pauseCount) || 0,
    continueCount: (window[GID] && window[GID].continueCount) || 0,
    captcha_gated: !!window[CGATE],
  }};
}}
"""

_SET_CAPTCHA_GATE_JS = f"""
(want) => {{
  const CGATE = {CAPTCHA_GATE_GLOBAL!r};
  const OID = {OVERLAY_ID!r};
  window[CGATE] = !!want;
  const root = document.getElementById(OID);
  if (root) {{
    if (want) {{
      root.classList.add('jh-captcha-gated');
      root.setAttribute('aria-hidden', 'true');
      root.setAttribute('data-jh-captcha-gated', '1');
    }} else {{
      root.classList.remove('jh-captcha-gated');
      root.removeAttribute('aria-hidden');
      root.removeAttribute('data-jh-captcha-gated');
    }}
  }}
  return {{ captcha_gated: !!window[CGATE], overlay_present: !!root }};
}}
"""

_READ_STATE_JS = f"""
() => {{
  const c = window[{CONTROL_GLOBAL!r}];
  if (!c) return {{ paused: false, installed: false, captcha_gated: !!window[{CAPTCHA_GATE_GLOBAL!r}] }};
  return {{
    paused: !!c.paused,
    installed: true,
    pauseCount: c.pauseCount || 0,
    continueCount: c.continueCount || 0,
    captcha_gated: !!window[{CAPTCHA_GATE_GLOBAL!r}],
  }};
}}
"""

_PUSH_ACTIVITY_JS = f"""
(payload) => {{
  const AGID = {ACTIVITY_GLOBAL!r};
  const OID = {OVERLAY_ID!r};
  window[AGID] = Object.assign({{}}, window[AGID] || {{}}, payload || {{}});
  const tip = document.getElementById(OID + '-tip');
  if (tip) {{
    const a = window[AGID];
    tip.textContent = a.text || [a.layer_label || a.layer || '—', a.action || 'idle']
      .concat(a.label ? [a.label] : [])
      .join(' · ');
  }}
  return {{ ok: true, text: (window[AGID] && window[AGID].text) || '' }};
}}
"""

_RESULTS_DIR = (
    Path(__file__).resolve().parents[2] / "skyvern_runtime" / "real_job_results"
)
_DEFAULT_PAUSE_SENTINEL = _RESULTS_DIR / ".fill_paused"
_DEFAULT_CONTINUE_SENTINEL = _RESULTS_DIR / ".fill_continue"


def resolve_fill_pause(*, headed: bool, fill_pause: bool | None = None) -> bool:
    """Headed default ON. Explicit flag / env wins."""
    if fill_pause is not None:
        return bool(fill_pause)
    env = (os.environ.get("FASTFILL_FILL_PAUSE") or "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return bool(headed)


def fill_pause_continue_sentinel_path() -> Path:
    env = (os.environ.get("FASTFILL_FILL_CONTINUE_FILE") or "").strip()
    return Path(env).expanduser() if env else _DEFAULT_CONTINUE_SENTINEL


def fill_pause_force_sentinel_path() -> Path:
    """Presence forces paused=true (optional external pause)."""
    env = (os.environ.get("FASTFILL_FILL_PAUSE_FILE") or "").strip()
    return Path(env).expanduser() if env else _DEFAULT_PAUSE_SENTINEL


def consume_fill_continue_sentinel() -> bool:
    path = fill_pause_continue_sentinel_path()
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
            return True
    except Exception:
        pass
    return False


def force_pause_sentinel_present() -> bool:
    try:
        return fill_pause_force_sentinel_path().is_file()
    except Exception:
        return False


def _page_inject_key(page) -> int:
    try:
        return id(page)
    except Exception:
        return 0


async def inject_fill_pause_overlay(
    page, *, force: bool = False, throttle_s: float = _INJECT_THROTTLE_S
) -> dict[str, Any]:
    """Install / refresh the top-right Pause button.

    FILL3-017: throttle repeated CDP evaluates when overlay was just injected
    (safe no-op for callers that poll often). Pass ``force=True`` to bypass.
    """
    key = _page_inject_key(page)
    now = time.monotonic()
    if not force and key in _last_inject_mono:
        if (now - _last_inject_mono[key]) < max(0.0, float(throttle_s)):
            return {"ok": True, "throttled": True}
    try:
        out = await page.evaluate(_INSTALL_OVERLAY_JS) or {"ok": False}
        _last_inject_mono[key] = now
        return out
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


async def set_fill_pause_captcha_gate(page, active: bool) -> dict[str, Any]:
    """FILL3-002 / FILL3-015: hide/disable overlay while CAPTCHA wait is active.

    Remount observer re-applies the gate. Never solves CAPTCHA.
    """
    try:
        # Ensure overlay exists so gate class can attach; force bypass throttle.
        await inject_fill_pause_overlay(page, force=True)
        return await page.evaluate(_SET_CAPTCHA_GATE_JS, bool(active)) or {}
    except Exception as e:
        return {"captcha_gated": bool(active), "error": str(e)[:120]}


async def install_fill_pause_on_context(context) -> None:
    """Survive navigations: inject on every new document."""
    try:
        await context.add_init_script(
            f"(() => {{ try {{ ({_INSTALL_OVERLAY_JS})(); }} catch (_) {{}} }})();"
        )
    except Exception:
        pass


async def push_fill_activity(page, act: dict[str, Any] | None = None) -> dict[str, Any]:
    """Push current activity into ``window.__jhFillActivity`` for the hover tip."""
    payload = dict(act or _CURRENT_ACTIVITY)
    payload["text"] = format_fill_activity_text(payload)
    if page is None:
        return {"ok": False, "via": "no_page", **payload}
    try:
        return await page.evaluate(_PUSH_ACTIVITY_JS, payload) or {"ok": False}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120], **payload}


async def read_fill_pause_state(
    page, *, assume_paused_on_error: bool | None = None
) -> dict[str, Any]:
    """Read overlay pause state.

    When ``assume_paused_on_error`` is True (used inside an active pause wait),
    or when the last successful read was paused, evaluate/CDP failures must NOT
    look like Continue — that would finish the fill loop and auto-close Chrome
    behind the human.
    """
    key = _page_inject_key(page)
    try:
        st = await page.evaluate(_READ_STATE_JS)
        if isinstance(st, dict):
            _last_paused_known[key] = bool(st.get("paused"))
            return st
    except Exception as e:
        last = _last_paused_known.get(key)
        keep_paused = bool(assume_paused_on_error) or bool(last)
        out: dict[str, Any] = {
            "installed": False,
            "error": str(e)[:120],
            "captcha_gated": False,
            "paused": keep_paused,
        }
        if keep_paused:
            out["via"] = "assume_paused_on_error"
        return out
    last = _last_paused_known.get(key)
    if assume_paused_on_error or last:
        return {
            "paused": True,
            "installed": False,
            "via": "assume_paused_on_error",
            "captcha_gated": False,
        }
    return {"paused": False, "installed": False}


async def set_fill_paused(page, paused: bool) -> dict[str, Any]:
    """Programmatic pause/resume (tests / sentinel). Updates overlay UI."""
    js = f"""
    (want) => {{
      const GID = {CONTROL_GLOBAL!r};
      const CGATE = {CAPTCHA_GATE_GLOBAL!r};
      if (!window[GID]) window[GID] = {{ paused: false, pauseCount: 0, continueCount: 0 }};
      const c = window[GID];
      const was = !!c.paused;
      c.paused = !!want;
      if (c.paused && !was) c.pauseCount = (c.pauseCount || 0) + 1;
      if (!c.paused && was) c.continueCount = (c.continueCount || 0) + 1;
      const btn = document.getElementById({OVERLAY_ID!r} + '-btn');
      const hint = document.getElementById({OVERLAY_ID!r} + '-hint');
      if (btn) {{
        if (c.paused) {{
          btn.textContent = 'Continue fill';
          btn.classList.add('jh-paused');
        }} else {{
          btn.textContent = 'Pause fill';
          btn.classList.remove('jh-paused');
        }}
      }}
      if (hint) {{
        hint.textContent = c.paused
          ? 'PAUSED between actions — edit fields, then Continue (skips already filled). Does NOT clear CAPTCHA wait.'
          : 'Pause takes effect between fill actions (not mid-widget). During CAPTCHA use Enter / .captcha_continue, not Continue fill.';
      }}
      return {{
        paused: !!c.paused,
        pauseCount: c.pauseCount,
        continueCount: c.continueCount,
        captcha_gated: !!window[CGATE],
      }};
    }}
    """
    try:
        await inject_fill_pause_overlay(page, force=True)
        out = await page.evaluate(js, bool(paused)) or {}
        _last_paused_known[_page_inject_key(page)] = bool(paused)
        return out
    except Exception as e:
        return {"paused": bool(paused), "error": str(e)[:120]}


def _note_pause(report: dict | None, **kwargs: Any) -> None:
    if report is None:
        return
    fp = report.setdefault("fill_pause", {})
    if not isinstance(fp, dict):
        fp = {}
        report["fill_pause"] = fp
    fp.update(kwargs)
    try:
        from fill_step_log import note_step

        note_step(
            report,
            action="fill_pause",
            reason=str(kwargs.get("event") or "pause")[:80],
            via="fill_pause_overlay",
        )
    except Exception:
        pass


async def wait_while_paused(
    page,
    report: dict | None = None,
    *,
    enabled: bool | None = None,
    poll_s: float = 0.25,
) -> dict[str, Any]:
    """Block while the in-page Pause button (or pause sentinel) is active.

    Returns a small status dict. No-op when overlay disabled / page missing.
    On resume after a pause, sets ``report['fill_pause']['resume_rescan']=True``
    so callers know to prefer already_correct skips.

    FILL3-002: when CAPTCHA gate is active, do not treat overlay pause as blocking
    (overlay is hidden; CAPTCHA wait owns the human channel).

    While paused, CDP/evaluate errors keep the wait (fail closed) so fill cannot
    finish and tear down headed Chrome behind the human.
    """
    out: dict[str, Any] = {
        "waited": False,
        "resumed": False,
        "via": None,
        "enabled": False,
    }
    if page is None:
        out["via"] = "no_page"
        return out

    if enabled is None:
        if report is not None and "fill_pause_enabled" in report:
            enabled = bool(report.get("fill_pause_enabled"))
        else:
            enabled = True
    out["enabled"] = bool(enabled)
    if not enabled:
        out["via"] = "disabled"
        return out

    try:
        await inject_fill_pause_overlay(page)
    except Exception:
        pass
    try:
        await push_fill_activity(page)
    except Exception:
        pass

    was_paused = False
    t0 = time.monotonic()
    while True:
        # FILL3-002 / FILL2-S02: CAPTCHA gate owns human resume — skip pause wait
        st_gate = await read_fill_pause_state(
            page, assume_paused_on_error=was_paused
        )
        if st_gate.get("captcha_gated"):
            out["via"] = "captcha_gated"
            return out

        if consume_fill_continue_sentinel():
            await set_fill_paused(page, False)
            if was_paused:
                out.update(
                    waited=True,
                    resumed=True,
                    via="sentinel",
                    waited_s=round(time.monotonic() - t0, 2),
                )
                _note_pause(
                    report,
                    event="resumed",
                    via="sentinel",
                    resume_rescan=True,
                    waited_s=out["waited_s"],
                )
                if report is not None:
                    report.setdefault("fill_pause", {})["resume_rescan"] = True
                note_fill_activity(layer="1", action="resumed", detail="continue sentinel")
            else:
                out["via"] = "sentinel_idle"
            return out

        if force_pause_sentinel_present():
            await set_fill_paused(page, True)

        st = await read_fill_pause_state(
            page, assume_paused_on_error=was_paused
        )
        if st.get("captcha_gated"):
            out["via"] = "captcha_gated"
            return out
        paused = bool(st.get("paused"))
        if not paused:
            if was_paused:
                out.update(
                    waited=True,
                    resumed=True,
                    via="overlay_continue",
                    waited_s=round(time.monotonic() - t0, 2),
                    pauseCount=st.get("pauseCount"),
                    continueCount=st.get("continueCount"),
                )
                _note_pause(
                    report,
                    event="resumed",
                    via="overlay_continue",
                    resume_rescan=True,
                    waited_s=out["waited_s"],
                )
                if report is not None:
                    report.setdefault("fill_pause", {})["resume_rescan"] = True
                note_fill_activity(
                    layer="1", action="resumed", detail="overlay continue"
                )
                print(
                    "[fill-pause] Continue — resuming fill "
                    "(will skip fields already filled)…",
                    flush=True,
                )
            else:
                out["via"] = "not_paused"
            return out

        if not was_paused:
            was_paused = True
            out["waited"] = True
            note_fill_activity(
                layer="paused",
                action="idle paused",
                detail="human editing form",
            )
            try:
                await push_fill_activity(page)
            except Exception:
                pass
            _note_pause(
                report,
                event="paused",
                via="overlay",
                pauseCount=st.get("pauseCount"),
            )
            print(
                "\n*** FILL PAUSED (between actions) — edit the form in Chrome, "
                "then click 'Continue fill' (top-right). "
                "Browser stays open until you Continue or close the window "
                "(never auto-closes while paused). "
                "Pause is not mid-widget; Continue fill does NOT clear a "
                "CAPTCHA wait — use Enter / .captcha_continue for CAPTCHA. ***\n",
                flush=True,
            )
            try:
                from captcha_pause import bring_chrome_testing_to_front

                bring_chrome_testing_to_front()
            except Exception:
                pass
        else:
            # Keep hover tip fresh while paused (cheap when throttled).
            try:
                await push_fill_activity(page)
            except Exception:
                pass

        await asyncio.sleep(max(0.1, float(poll_s)))


async def drain_pause_before_close(
    page,
    report: dict | None = None,
) -> dict[str, Any]:
    """Block until Pause is cleared before any terminal teardown / hold.

    If fill finished all fields while the human still had Pause engaged, we
    must wait here — never proceed to ``browser.close()``.
    """
    out: dict[str, Any] = {"drained": False}
    if page is None:
        out["via"] = "no_page"
        return out
    note_fill_activity(
        layer="hold",
        action="waiting pause drain",
        detail="terminal — keep browser open while paused",
    )
    try:
        await push_fill_activity(page)
    except Exception:
        pass
    try:
        out["paused_wait"] = await wait_while_paused(page, report)
        out["drained"] = True
    except Exception as e:
        out["error"] = str(e)[:120]
    # Human may re-pause during the edge; drain once more.
    try:
        st = await read_fill_pause_state(page, assume_paused_on_error=False)
        if st.get("paused"):
            out["paused_wait_2"] = await wait_while_paused(page, report)
    except Exception:
        pass
    return out


async def ensure_fill_pause_ready(page, report: dict | None = None) -> None:
    """Inject overlay once when headed fill starts; record enabled flag."""
    if report is not None and not report.get("fill_pause_enabled", True):
        return
    info = await inject_fill_pause_overlay(page, force=True)
    if report is not None:
        fp = report.setdefault("fill_pause", {})
        if isinstance(fp, dict):
            fp["overlay"] = info
            fp.setdefault("enabled", True)
