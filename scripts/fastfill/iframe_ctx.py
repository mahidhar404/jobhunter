"""Iframe / SPA-aware form discovery for fast fill.

Classic iCIMS and many white-label career pages put the apply form inside an
iframe (or delay it after Apply in a SPA). Playwright's page.evaluate only sees
the top document — so extract_form_fields / selector packs must also run on
child frames.

Never submits. Callers still gate every click via button_gate.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

# Common apply-iframe signatures (src / id / name / title)
_IFRAME_HINTS = re.compile(
    r"icims|apply|application|ats|greenhouse|lever|workday|taleo|"
    r"smartrecruiters|jobvite|bamboohr|successfactors|phenom|myworkday",
    re.I,
)

# Media / tracking embeds that must NEVER win pick_fill_context (Ashby JD pages
# mount Vimeo + embedded-media iframes with 0 form inputs but still rank first
# when the Application tab hasn't painted yet).
_NOISE_FRAME_RE = re.compile(
    r"embedded-media\.ashbyhq\.com|player\.vimeo\.com|vimeo\.com/|"
    r"youtube\.com|youtu\.be|doubleclick\.|googletagmanager|"
    r"facebook\.com/plugins|platform\.twitter|"
    r"challenges\.cloudflare\.com|turnstile",
    re.I,
)

# Job-alert / newsletter / chatbot widgets on Phenom-style JD pages expose a
# lone email input — must NOT count as "form reached" or we skip Apply entirely.
_NOISE_WIDGET_JS = """
  const _noiseRe = /notif|alert|newsletter|job.?alert|talent.?communit|subscribe|mailing.?list|chatbot|similar.?jobs|get.?notified/;
  const isNoiseWidget = (el) => {
    if (!el) return true;
    const form = el.closest && el.closest('form');
    const region = el.closest && el.closest(
      '[class*="alert" i], [id*="alert" i], [class*="notif" i], [id*="notif" i],' +
      '[class*="newsletter" i], [class*="talent" i], [class*="chatbot" i],' +
      '[aria-label*="alert" i], [aria-label*="notif" i], [data-ph-at-id*="jobAlert" i]'
    );
    const blob = [
      el.id || '', el.name || '', el.placeholder || '', el.className || '',
      el.getAttribute('aria-label') || '',
      form ? ((form.id || '') + ' ' + (form.className || '') + ' ' + (form.getAttribute('aria-label') || '') + ' ' + (form.innerText || '').slice(0, 120)) : '',
      region ? ((region.id || '') + ' ' + (region.className || '') + ' ' + (region.getAttribute('aria-label') || '')) : '',
    ].join(' ').toLowerCase();
    return _noiseRe.test(blob);
  };
"""

_FORM_INPUT_JS = """() => {
""" + _NOISE_WIDGET_JS + """
  const inputs = Array.from(document.querySelectorAll(
    'input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea, select'
  ));
  return inputs.filter(el => {
    if (isNoiseWidget(el)) return false;
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  }).length;
}"""

_FORM_EVIDENCE_JS = """(sels) => {
""" + _NOISE_WIDGET_JS + """
  const hit = [];
  for (const sel of sels) {
    try {
      const nodes = Array.from(document.querySelectorAll(sel));
      for (const el of nodes) {
        if (isNoiseWidget(el)) continue;
        const s = window.getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') continue;
        const r = el.getBoundingClientRect();
        if (r.width > 2 && r.height > 2) { hit.push(sel); break; }
      }
    } catch (e) {}
  }
  return hit;
}"""

# Soft evidence alone (bare email) is common on JD pages; require hard contact
# fields OR an apply-path URL before declaring form reached.
_SOFT_EVIDENCE_RES = (
    re.compile(r"input\[type=['\"]?email", re.I),
    re.compile(r"autocomplete=['\"]?email", re.I),
)


def _frame_hint(frame) -> str:
    try:
        url = frame.url or ""
    except Exception:
        url = ""
    try:
        name = frame.name or ""
    except Exception:
        name = ""
    return f"{name}|{url}"


def _frame_looks_apply(frame) -> bool:
    hint = _frame_hint(frame)
    if _NOISE_FRAME_RE.search(hint):
        return False
    if _IFRAME_HINTS.search(hint):
        return True
    # nameless about:blank children often host SPA apply widgets
    try:
        url = (frame.url or "").lower()
        if url in ("", "about:blank", "about:srcdoc"):
            return True
    except Exception:
        pass
    return False


def _is_noise_frame(frame_or_hint) -> bool:
    if isinstance(frame_or_hint, str):
        return bool(_NOISE_FRAME_RE.search(frame_or_hint))
    return bool(_NOISE_FRAME_RE.search(_frame_hint(frame_or_hint)))


async def frame_form_signal(frame, evidence_selectors: list[str] | None = None) -> dict:
    """Count visible inputs (+ optional evidence selectors) inside a frame/page.

    Ignores job-alert / newsletter / chatbot email widgets so Phenom-style JD
    pages do not look like an application form before Apply is clicked.
    """
    out: dict[str, Any] = {
        "url": "",
        "name": "",
        "visible_input_count": 0,
        "evidence": [],
        "reached": False,
        "error": None,
    }
    try:
        out["url"] = frame.url or ""
        out["name"] = getattr(frame, "name", None) or ""
    except Exception as e:
        out["error"] = str(e)[:120]
        return out
    try:
        out["visible_input_count"] = int(await frame.evaluate(_FORM_INPUT_JS))
    except Exception as e:
        out["error"] = str(e)[:120]
        return out
    if evidence_selectors:
        try:
            out["evidence"] = await frame.evaluate(_FORM_EVIDENCE_JS, evidence_selectors[:40])
        except Exception:
            out["evidence"] = []

    evidence = list(out["evidence"] or [])
    hard = [e for e in evidence if not any(rx.search(e) for rx in _SOFT_EVIDENCE_RES)]
    soft = [e for e in evidence if any(rx.search(e) for rx in _SOFT_EVIDENCE_RES)]
    url_l = (out.get("url") or "").lower()
    on_apply_path = bool(
        re.search(r"/apply|/application|/candidate|/login|jobapplication", url_l)
    )
    # Reached when: hard evidence, enough real inputs, soft email on apply/login
    # path, or apply-looking frame with ≥1 real input (classic iframe ATS).
    if hard or out["visible_input_count"] >= 3:
        out["reached"] = True
    elif soft and (on_apply_path or out["visible_input_count"] >= 2):
        out["reached"] = True
    else:
        out["reached"] = False
    return out


async def list_fill_contexts(page, evidence_selectors: list[str] | None = None) -> list[dict]:
    """Rank page + child frames by form-field signal (best first)."""
    contexts: list[dict] = []
    # Main page first (may already have fields)
    try:
        main = await frame_form_signal(page, evidence_selectors)
        main["kind"] = "page"
        main["frame"] = page
        main["is_main"] = True
        contexts.append(main)
    except Exception as e:
        contexts.append({
            "kind": "page",
            "frame": page,
            "is_main": True,
            "reached": False,
            "visible_input_count": 0,
            "error": str(e)[:120],
        })

    try:
        frames = list(page.frames)
    except Exception:
        frames = []

    for fr in frames:
        if fr == page.main_frame:
            continue
        # Skip media / tracking embeds (Ashby Vimeo, embedded-media, …)
        hint = _frame_hint(fr)
        if _is_noise_frame(hint):
            continue
        # Skip tiny tracking / ads when URL is clearly unrelated
        if not _frame_looks_apply(fr) and "javascript:" in hint.lower():
            continue
        try:
            sig = await frame_form_signal(fr, evidence_selectors)
        except Exception as e:
            sig = {
                "reached": False,
                "visible_input_count": 0,
                "error": str(e)[:120],
                "url": "",
                "name": "",
            }
        sig["kind"] = "frame"
        sig["frame"] = fr
        sig["is_main"] = False
        sig["hint"] = hint[:200]
        # Prefer apply-looking frames even with fewer inputs (iCIMS often 2–4 early)
        if _frame_looks_apply(fr) and sig["visible_input_count"] >= 1:
            sig["reached"] = True
        contexts.append(sig)

    def _score(c: dict) -> tuple:
        url_l = (c.get("url") or c.get("hint") or "").lower()
        on_app = 1 if re.search(r"/application|/apply", url_l) else 0
        return (
            1 if c.get("reached") else 0,
            c.get("visible_input_count") or 0,
            on_app,
            1 if c.get("kind") == "frame" and _IFRAME_HINTS.search(c.get("hint") or c.get("url") or "") else 0,
            0 if c.get("is_main") else 1,  # prefer frame when tied (iframe ATS)
        )

    contexts.sort(key=_score, reverse=True)
    return contexts


async def pick_fill_context(page, evidence_selectors: list[str] | None = None) -> dict:
    """Return the best page/frame context for extract + pack fill."""
    ranked = await list_fill_contexts(page, evidence_selectors)
    best = ranked[0] if ranked else {
        "frame": page,
        "kind": "page",
        "is_main": True,
        "reached": False,
        "visible_input_count": 0,
    }
    best["candidates"] = [
        {
            "kind": c.get("kind"),
            "url": (c.get("url") or "")[:160],
            "name": c.get("name"),
            "visible_input_count": c.get("visible_input_count"),
            "reached": c.get("reached"),
            "hint": (c.get("hint") or "")[:120],
        }
        for c in ranked[:8]
    ]
    return best


async def wait_for_form_spa(
    page,
    *,
    evidence_selectors: list[str] | None = None,
    timeout_ms: int = 18000,
    poll_ms: int = 900,
    clicked_apply: bool = False,
) -> dict:
    """Poll top page + child frames until a form appears (SPA / delayed iframe).

    After Apply on RockCo / Phenom / Serco-style career sites the form may:
      - paint after a network round-trip
      - live in an iframe that mounts late
      - navigate same-tab to /apply (jobSeqNo / apply?…)
    """
    report: dict[str, Any] = {
        "reached": False,
        "waited_ms": 0,
        "polls": 0,
        "context": None,
        "navigations": [],
        "iframe_tried": False,
    }
    if not clicked_apply and timeout_ms > 4000:
        # Short poll when we never clicked Apply — form may already be there
        timeout_ms = min(timeout_ms, 5000)

    t0 = time.time()
    start_url = page.url
    best: dict | None = None

    while (time.time() - t0) * 1000 < timeout_ms:
        report["polls"] += 1
        try:
            if page.is_closed():
                report["error"] = "page_closed"
                break
        except Exception:
            report["error"] = "page_closed"
            break
        # Same-page navigation detection (JD → /apply SPA)
        try:
            if page.url != start_url:
                report["navigations"].append(page.url[:200])
                start_url = page.url
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception:
                    pass
                # Extra settle after /apply navigation
                if re.search(r"/apply|/application", (page.url or "").lower()):
                    await asyncio.sleep(1.2)
        except Exception:
            pass

        try:
            ctx = await pick_fill_context(page, evidence_selectors)
        except Exception as e:
            if "closed" in str(e).lower() or "TargetClosed" in type(e).__name__:
                report["error"] = "page_closed"
                break
            raise
        best = ctx
        report["iframe_tried"] = any(
            c.get("kind") == "frame" for c in (ctx.get("candidates") or [])
        )
        if ctx.get("reached"):
            report["reached"] = True
            report["context"] = {
                "kind": ctx.get("kind"),
                "url": (ctx.get("url") or "")[:200],
                "visible_input_count": ctx.get("visible_input_count"),
                "candidates": ctx.get("candidates"),
            }
            report["waited_ms"] = int((time.time() - t0) * 1000)
            report["fill_target"] = ctx.get("frame")
            return report

        # Prefer asyncio.sleep — page.wait_for_timeout dies if the human closes the tab
        await asyncio.sleep(max(0.05, poll_ms / 1000.0))

    report["waited_ms"] = int((time.time() - t0) * 1000)
    if best:
        report["context"] = {
            "kind": best.get("kind"),
            "url": (best.get("url") or "")[:200],
            "visible_input_count": best.get("visible_input_count"),
            "candidates": best.get("candidates"),
        }
        report["fill_target"] = best.get("frame")
        report["reached"] = bool(best.get("reached"))
    else:
        report["fill_target"] = page
    return report


# ---------------------------------------------------------------------------
# Login / create-account continuation (iCIMS iframe Apply → auth gate)
# ---------------------------------------------------------------------------

_LOGIN_URL_HINT = re.compile(r"/login|/signin|/sign-in|/register|/create.?account", re.I)
_EMAIL_VERIFY_HINTS = (
    "check your email",
    "verify your email",
    "verification email",
    "verification link",
    "confirm your email",
    "we've sent",
    "we have sent",
    "email has been sent",
    "activate your account",
    "complete your registration",
)

_AUTH_EMAIL_SELS = [
    "input#email",
    "input[name='css_loginName']",
    "input[name*='loginName' i]",
    "input[name='PersonProfileFields.Login']",
    "input[id='PersonProfileFields.Login']",
    "input[type=email]",
    "input[autocomplete='email']",
    "input[name*='email' i]",
    "input[id*='email' i]",
]

_AUTH_ADVANCE_PRIORITY = (
    "create account",
    "create an account",
    "create your account",
    "sign up",
    "register",
    "next",
    "continue",
    "sign in",
    "log in",
)

# Labels that mean "leave Sign in, go create a throwaway account" (Stripe /
# MyGreenhouse / Phenom). Prefer these over filling/clicking Sign in.
_CREATE_ACCOUNT_LINK_PRIORITY = (
    "create account",
    "create an account",
    "create your account",
    "sign up",
    "register",
    "don't have an account",
    "dont have an account",
    "new to stripe",
    "new here",
)

# Body / title phrases that mean "you are on Sign in, not the job application".
_SIGN_IN_WALL_BODY_HINTS = (
    "sign in to your account",
    "sign in to continue",
    "log in to your account",
    "already have an account",
    "forgot your password",
    "remember me",
    "remember me on this device",
    "or sign in with",
)

# Hosts that are product auth, never the ATS application form.
_PRODUCT_AUTH_HOST_RE = re.compile(
    r"(?:^|\.)dashboard\.stripe\.com$|(?:^|\.)account\.stripe\.com$",
    re.I,
)


def normalize_auth_label(text: str) -> str:
    """Lowercase label; strip trailing punctuation (Stripe uses ``Sign in.``)."""
    low = (text or "").lower().strip()
    return re.sub(r"[.\u2026]+$", "", low).strip()


def create_account_link_priority(text: str, href: str = "") -> int | None:
    """Return priority (lower=better) if label/href is a create-account control.

    Returns None for pure Sign-in controls or unrelated text.
    """
    low = normalize_auth_label(text)
    if not low and not href:
        return None
    # Skip pure Sign in / SSO buttons
    if low in ("sign in", "log in", "sign-in", "log-in") or low.startswith(
        ("sign in with", "log in with", "continue with")
    ):
        return None
    if low in ("google", "passkey", "sso", "apple", "microsoft"):
        return None
    pri: int | None = None
    for i, needle in enumerate(_CREATE_ACCOUNT_LINK_PRIORITY):
        if needle in low or low == needle or low.startswith(needle):
            pri = i if pri is None else min(pri, i)
            break
    href_l = str(href or "").lower()
    if any(h in href_l for h in ("/register", "/signup", "/sign-up", "create", "join")):
        pri = 2 if pri is None else min(pri, 2)
    return pri


def auth_advance_priority(text: str) -> int | None:
    """Priority for auth ADVANCE controls; Sign in demoted so Create wins."""
    low = normalize_auth_label(text)
    if not low:
        return None
    pri: int | None = None
    for i, needle in enumerate(_AUTH_ADVANCE_PRIORITY):
        if (
            low == needle
            or low.startswith(needle + " ")
        ):
            pri = i
            break
    if pri is None:
        return None
    # Demote Sign in / Log in so create-account always wins when both present
    if low in ("sign in", "log in") or low.startswith(("sign in ", "log in ")):
        pri = 80 + pri
    return pri


def sign_in_wall_from_signals(
    *,
    body: str = "",
    url: str = "",
    title: str = "",
    email_count: int = 0,
    password_count: int = 0,
    appish_count: int = 0,
) -> bool:
    """True for Sign-in-only gates (Stripe / MyGreenhouse): not the app form.

    Pure helper for tests + ``is_password_sign_in_wall``. Email-only walls with
    ``Sign in to your account`` count even before the password field paints.
    """
    if int(appish_count or 0) >= 1:
        return False
    url_l = (url or "").lower()
    host = ""
    try:
        from urllib.parse import urlparse

        host = (urlparse(url_l).hostname or "").lower()
    except Exception:
        host = ""
    if host and _PRODUCT_AUTH_HOST_RE.search(host):
        # dashboard.stripe.com/login — never treat as application pack target
        return True
    if _LOGIN_URL_HINT.search(url_l) and int(password_count or 0) >= 1:
        return True
    blob = f"{title}\n{body}".lower()
    has_wall_copy = any(h in blob for h in _SIGN_IN_WALL_BODY_HINTS)
    if has_wall_copy and int(appish_count or 0) == 0:
        # Email-only (password not painted yet) or email+password auth card
        if int(email_count or 0) >= 1 or int(password_count or 0) >= 1:
            return True
        # Heading alone on a login URL (fields still loading)
        if _LOGIN_URL_HINT.search(url_l) or "sign in to your account" in blob:
            return True
    if int(password_count or 0) >= 1 and int(email_count or 0) >= 1:
        return True
    return False


def looks_like_login_context(
    url: str = "", *, password_count: int = 0, email_count: int = 0
) -> bool:
    """True when the fill target is an auth gate (login/create), not the app form."""
    if _LOGIN_URL_HINT.search(url or ""):
        return True
    try:
        from urllib.parse import urlparse

        host = (urlparse(url or "").hostname or "").lower()
        if host and _PRODUCT_AUTH_HOST_RE.search(host):
            return True
    except Exception:
        pass
    if password_count >= 1 and email_count >= 1 and password_count + email_count <= 4:
        return True
    return False


def detect_auth_blocker(page_text: str, title: str = "", url: str = "") -> str | None:
    """CAPTCHA / email-verify — never solve; report honestly.

    Footer-only 'Protected by hCaptcha' is common on iCIMS even when the
    challenge is not yet interactive — callers should prefer
    ``visible_captcha_challenge`` for hard stops.
    """
    blob = f"{title}\n{url}\n{page_text}".lower()
    strong_captcha = (
        "complete the captcha",
        "solve the captcha",
        "captcha required",
        "verify you are human",
        "i'm not a robot",
        "i am not a robot",
        "cf-challenge",
        "challenge-platform",
    )
    if any(h in blob for h in strong_captcha):
        return "captcha"
    if "hcaptcha challenge" in blob or ("recaptcha" in blob and "challenge" in blob):
        return "captcha"
    if any(h in blob for h in _EMAIL_VERIFY_HINTS):
        return "email_verify"
    return None


async def visible_captcha_challenge(frame) -> bool:
    """True when an interactive CAPTCHA *challenge* overlay is on-screen.

    Checkbox / anchor widgets (``.h-captcha iframe``, reCAPTCHA anchor,
    dormant Turnstile) stay on Lever/iCIMS/GH after solve and must NOT count —
    otherwise Continue is stuck forever with ``challenge still visible``.
    Only challenge/bframe/cf-challenge iframes that are laid out + visible.
    """
    try:
        return bool(
            await frame.evaluate(
                """() => {
                  const visibleBox = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 120 || r.height < 60) return false;
                    if (r.bottom <= 0 || r.right <= 0) return false;
                    try {
                      const s = window.getComputedStyle(el);
                      if (s.display === 'none' || s.visibility === 'hidden') return false;
                      if (Number(s.opacity) === 0) return false;
                    } catch (_) {}
                    return true;
                  };
                  const isChallengeSrc = (blob) =>
                    /hcaptcha\\.com[^\\s]*challenge|recaptcha[^\\s]*\\/bframe|challenges\\.cloudflare|cf-challenge|turnstile.*challenge/i
                      .test(blob || '');
                  for (const f of document.querySelectorAll('iframe')) {
                    const src = ((f.src || '') + ' ' + (f.title || '')).trim();
                    if (isChallengeSrc(src) && visibleBox(f)) return true;
                    // Generic: captcha vendor + challenge token in src/title
                    if (/hcaptcha|recaptcha|captcha|turnstile/i.test(src)
                        && /challenge|bframe/i.test(src)
                        && visibleBox(f)) return true;
                  }
                  return false;
                }"""
            )
        )
    except Exception:
        return False


async def _count_auth_inputs(frame) -> dict[str, int]:
    try:
        return await frame.evaluate(
            """() => {
              const vis = (el) => {
                const s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') return false;
                const r = el.getBoundingClientRect();
                return r.width > 2 && r.height > 2;
              };
              const emails = Array.from(document.querySelectorAll(
                "input#email, input[name='css_loginName'], input[type=email], input[autocomplete='email'], input[name*='email' i], input[name*='login' i]"
              )).filter(vis).length;
              const passwords = Array.from(document.querySelectorAll(
                "input[type=password]"
              )).filter(vis).length;
              const appish = Array.from(document.querySelectorAll(
                "input[name*='firstname' i], input[id*='firstname' i], input[autocomplete='given-name'], input[name*='PersonProfileFields.FirstName' i], input[type=file], input[name*='phone' i]"
              )).filter(vis).length;
              return {email: emails, password: passwords, appish};
            }"""
        )
    except Exception:
        return {"email": 0, "password": 0, "appish": 0}


async def _frame_body_snip(frame, n: int = 2500) -> str:
    try:
        return await frame.evaluate(
            f"() => (document.body && document.body.innerText || '').slice(0, {int(n)})"
        )
    except Exception:
        return ""


async def _fill_visible(frame, selectors: list[str], value: str) -> dict | None:
    """Fill first visible matching selector; verify readback for non-password."""
    for sel in selectors:
        try:
            loc = frame.locator(sel)
            n = await loc.count()
        except Exception:
            continue
        for i in range(min(n, 4)):
            item = loc.nth(i)
            try:
                if not await item.is_visible(timeout=400):
                    continue
                await item.fill(str(value), timeout=4000)
                itype = ((await item.get_attribute("type")) or "").lower()
                if itype != "password":
                    got = await item.input_value()
                    if str(got).strip() != str(value).strip():
                        continue
                return {
                    "ok": True,
                    "selector": sel,
                    "value": value if itype != "password" else "***",
                }
            except Exception:
                continue
    return None


async def _fill_passwords(frame, password: str) -> list[dict]:
    """Fill visible password inputs: first=PASSWORD, second=CONFIRM (same dummy)."""
    filled: list[dict] = []
    try:
        loc = frame.locator("input[type=password]")
        n = await loc.count()
    except Exception:
        return filled
    for i in range(min(n, 3)):
        item = loc.nth(i)
        try:
            if not await item.is_visible(timeout=400):
                continue
            await item.fill(str(password), timeout=4000)
            ftype = "PASSWORD_CONFIRM" if len(filled) >= 1 else "PASSWORD"
            filled.append(
                {
                    "ok": True,
                    "type": ftype,
                    "selector": f"input[type=password]:nth({i})",
                    "value": "***",
                    "via": "iframe_login",
                }
            )
        except Exception as e:
            filled.append(
                {
                    "ok": False,
                    "type": "PASSWORD" if not filled else "PASSWORD_CONFIRM",
                    "error": str(e)[:120],
                    "via": "iframe_login",
                }
            )
    return filled


async def _pick_auth_advance(frame) -> dict | None:
    """Pick Create Account / Next / Sign In via button_map; never FINAL."""
    from button_gate import gate_click
    from button_map import ADVANCE, classify_button

    try:
        raw = await frame.evaluate(
            """() => {
              const sel = 'button, a[href], input[type=button], input[type=submit], [role=button]';
              const out = [];
              const seen = new Set();
              for (const el of document.querySelectorAll(sel)) {
                const s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                const r = el.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) continue;
                let label = (el.innerText || el.value || el.getAttribute('aria-label')
                             || el.getAttribute('title') || '').trim();
                label = label.replace(/\\s+/g, ' ').slice(0, 120);
                if (!label) continue;
                const key = label.toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({
                  text: label,
                  type: (el.getAttribute('type') || ''),
                  aria_label: (el.getAttribute('aria-label') || ''),
                });
              }
              return out;
            }"""
        )
    except Exception:
        return None

    scored: list[tuple[int, dict]] = []
    for c in raw or []:
        text = c.get("text") or ""
        kind = classify_button(
            text, button_type=c.get("type") or "", aria_label=c.get("aria_label") or ""
        )
        gate = gate_click(
            text, button_type=c.get("type") or "", aria_label=c.get("aria_label") or ""
        )
        if not gate.get("ok") or kind != ADVANCE:
            continue
        pri = auth_advance_priority(text)
        if pri is None:
            continue
        scored.append((pri, {**c, "kind": kind, "gate_ok": True}))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    return scored[0][1]


async def _gated_auth_click(
    frame, host_page, ctrl: dict, *, allow_unknown: bool = False
) -> bool:
    """Click ADVANCE auth control; re-gate resolved node. Never FINAL.

    ``has-text("Continue")`` can match ``Continue to Submit`` — refuse via
    ``gate_locator_click`` on the actual element before any click.
    ``allow_unknown``: create-account links like \"Don't have an account?\".
    """
    import json as _json

    from button_gate import gate_click, gate_locator_click
    from button_map import ADVANCE, FINAL, UNKNOWN

    text = ctrl.get("text") or ""
    gate = gate_click(
        text,
        button_type=ctrl.get("type") or "",
        aria_label=ctrl.get("aria_label") or "",
    )
    if not gate.get("ok") or gate.get("kind") == FINAL:
        return False

    allow = (ADVANCE, UNKNOWN) if allow_unknown else (ADVANCE,)

    candidates = [
        frame.get_by_role("button", name=re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)),
        frame.get_by_role("link", name=re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)),
        frame.locator(f"button:has-text({_json.dumps(text)})"),
        frame.locator(f"a:has-text({_json.dumps(text)})"),
        frame.locator(f"input[type=submit][value={_json.dumps(text)}]"),
        frame.locator(f"input[type=button][value={_json.dumps(text)}]"),
    ]
    for loc in candidates:
        try:
            n = await loc.count()
        except Exception:
            continue
        for i in range(min(n, 8)):
            try:
                target = loc.nth(i)
                if not await target.is_visible(timeout=800):
                    continue
                resolved = await gate_locator_click(
                    target, intent_label=text, allow_kinds=allow
                )
                if not resolved.get("ok") or resolved.get("kind") == FINAL:
                    continue
                await target.click(timeout=5000)
                try:
                    await host_page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                await host_page.wait_for_timeout(1800)
                return True
            except Exception:
                continue
    return False


async def is_password_sign_in_wall(frame) -> bool:
    """True for Sign-in-only gates (Stripe / MyGreenhouse): not the app form.

    Includes email-only ``Sign in to your account`` walls (password not painted
    yet) and product hosts like ``dashboard.stripe.com``.
    """
    counts = await _count_auth_inputs(frame)
    if int(counts.get("appish") or 0) >= 1:
        return False
    try:
        url = frame.url or ""
    except Exception:
        url = ""
    body = await _frame_body_snip(frame, 1800)
    title = ""
    try:
        # Frame may be a Page
        if hasattr(frame, "title"):
            title = await frame.title()
        elif hasattr(frame, "page") and frame.page is not None:
            title = await frame.page.title()
    except Exception:
        title = ""
    return sign_in_wall_from_signals(
        body=body,
        url=url,
        title=title,
        email_count=int(counts.get("email") or 0),
        password_count=int(counts.get("password") or 0),
        appish_count=int(counts.get("appish") or 0),
    )


async def _pick_create_account_link(frame) -> dict | None:
    """Find Create account / Sign up control (link or button), never Sign in."""
    from button_gate import gate_click
    from button_map import ADVANCE, classify_button

    try:
        raw = await frame.evaluate(
            """() => {
              const sel = 'button, a[href], input[type=button], input[type=submit], [role=button], span[role=link]';
              const out = [];
              const seen = new Set();
              for (const el of document.querySelectorAll(sel)) {
                const s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                const r = el.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) continue;
                let label = (el.innerText || el.value || el.getAttribute('aria-label')
                             || el.getAttribute('title') || '').trim();
                label = label.replace(/\\s+/g, ' ').slice(0, 120);
                if (!label) continue;
                const key = label.toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({
                  text: label,
                  type: (el.getAttribute('type') || ''),
                  aria_label: (el.getAttribute('aria-label') || ''),
                  href: (el.getAttribute('href') || ''),
                });
              }
              return out;
            }"""
        )
    except Exception:
        return None

    scored: list[tuple[int, dict]] = []
    for c in raw or []:
        text = c.get("text") or ""
        href = str(c.get("href") or "")
        pri = create_account_link_priority(text, href)
        if pri is None:
            continue
        kind = classify_button(
            text, button_type=c.get("type") or "", aria_label=c.get("aria_label") or ""
        )
        gate = gate_click(
            text, button_type=c.get("type") or "", aria_label=c.get("aria_label") or ""
        )
        # Create-account links may classify as ADVANCE or UNKNOWN — allow both if gated ok
        # or text clearly create-account (gate_click may refuse UNKNOWN).
        if gate.get("ok") or kind == ADVANCE or pri <= 4:
            scored.append((pri, {**c, "kind": kind or ADVANCE, "gate_ok": True}))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    return scored[0][1]


async def ensure_create_account_over_sign_in(
    page,
    fill_target=None,
) -> dict:
    """If stuck on Sign in wall, click Create account before filling credentials.

    Stripe careers / dashboard auth and similar gates default to Sign in; dummy
    runs must create a throwaway account instead of signing into a missing one.
    """
    out: dict[str, Any] = {
        "ran": False,
        "is_sign_in_wall": False,
        "switched": False,
        "clicked": None,
        "skipped": None,
    }
    target = fill_target or page
    try:
        if fill_target is None:
            ctx = await pick_fill_context(page)
            target = ctx.get("frame") or page
    except Exception:
        target = fill_target or page

    if not await is_password_sign_in_wall(target):
        # Also check top page (auth may be main frame while fill_target is stale)
        if target is not page and await is_password_sign_in_wall(page):
            target = page
        else:
            out["skipped"] = "not_sign_in_wall"
            return out

    out["is_sign_in_wall"] = True
    out["ran"] = True
    ctrl = await _pick_create_account_link(target)
    if not ctrl and target is not page:
        ctrl = await _pick_create_account_link(page)
        if ctrl:
            target = page
    if not ctrl:
        out["skipped"] = "no_create_account_control"
        return out

    host = page
    try:
        if hasattr(target, "page") and target.page is not None:
            host = target.page
    except Exception:
        pass

    ok = await _gated_auth_click(target, host, ctrl, allow_unknown=True)
    out["clicked"] = {"text": ctrl.get("text"), "href": ctrl.get("href"), "ok": bool(ok)}
    out["switched"] = bool(ok)
    if ok:
        try:
            await host.wait_for_timeout(1800)
            await host.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        try:
            ctx = await pick_fill_context(host)
            out["fill_target"] = ctx.get("frame") or host
        except Exception:
            out["fill_target"] = host
    return out


def create_account_sentinel_path() -> Path:
    """Optional monitor/corrector sentinel: force create-account click path."""
    env = (os.environ.get("FASTFILL_CREATE_ACCOUNT_FILE") or "").strip()
    if env:
        return Path(env).expanduser()
    # Prefer attempt-scoped captcha continue sibling when set
    cap = (os.environ.get("FASTFILL_CAPTCHA_CONTINUE_FILE") or "").strip()
    if cap:
        return Path(cap).expanduser().parent / ".force_create_account"
    root = Path(__file__).resolve().parents[2] / "skyvern_runtime" / "real_job_results"
    return root / ".force_create_account"


def consume_create_account_sentinel() -> bool:
    """True once if monitor/human requested create-account corrective click."""
    path = create_account_sentinel_path()
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
            return True
    except Exception:
        pass
    return False


async def run_auth_gate_before_pack(
    page,
    values: dict,
    *,
    fill_target=None,
    max_rounds: int = 2,
    force: bool = False,
) -> dict:
    """Pre-pack auth gate: Create account over Sign in, then fill credentials.

    When still on a pure password/sign-in wall with no app fields, callers must
    **skip the application selector pack** (``skip_app_pack``).
    Never submits. Never solves CAPTCHA.
    """
    out: dict[str, Any] = {
        "ran": False,
        "create_account": None,
        "iframe_login": None,
        "skip_app_pack": False,
        "is_sign_in_wall": False,
        "fill_target": fill_target,
        "forced": bool(force),
    }
    target = fill_target or page
    wall = False
    try:
        wall = await is_password_sign_in_wall(target)
        if not wall and target is not page:
            wall = await is_password_sign_in_wall(page)
            if wall:
                target = page
    except Exception:
        wall = False

    # Also treat classic login URL + email/password as auth (iCIMS iframe)
    if not wall:
        try:
            url = getattr(target, "url", None) or page.url or ""
        except Exception:
            url = ""
        counts = await _count_auth_inputs(target)
        if looks_like_login_context(
            url,
            password_count=counts.get("password", 0),
            email_count=counts.get("email", 0),
        ):
            wall = True

    if not wall and not force:
        out["skipped"] = "not_auth_wall"
        return out

    out["ran"] = True
    out["is_sign_in_wall"] = bool(wall) or bool(force)

    ca = await ensure_create_account_over_sign_in(page, fill_target=target)
    out["create_account"] = {
        k: v for k, v in ca.items() if k != "fill_target"
    }
    if ca.get("fill_target") is not None:
        target = ca["fill_target"]
        out["fill_target"] = target

    auth = await continue_iframe_login(
        page, values, fill_target=target, max_rounds=max_rounds
    )
    out["iframe_login"] = {k: v for k, v in auth.items() if k != "fill_target"}
    if auth.get("fill_target") is not None:
        target = auth["fill_target"]
        out["fill_target"] = target

    # Still a pure sign-in wall → do not run Greenhouse/app selector pack
    still_wall = False
    try:
        still_wall = await is_password_sign_in_wall(target)
        if not still_wall and target is not page:
            still_wall = await is_password_sign_in_wall(page)
    except Exception:
        still_wall = False
    reached_app = bool(auth.get("reached_app_fields"))
    out["skip_app_pack"] = bool(still_wall and not reached_app)
    if out["skip_app_pack"] and not auth.get("blocker"):
        # Honest stop: dummy has no Stripe product account to sign into
        if ca.get("skipped") == "no_create_account_control":
            out.setdefault("blocker", "sign_in_only_no_create")
        else:
            out.setdefault("blocker", "login_wall")
    if auth.get("blocker"):
        out["blocker"] = auth["blocker"]
    return out


async def continue_iframe_login(
    page,
    values: dict,
    *,
    fill_target=None,
    max_rounds: int = 2,
) -> dict:
    """After iframe Apply: fill dummy email(+password), gated auth ADVANCE.

    Never submits the application. Never solves CAPTCHA. Stops on email_verify.
    ``values`` must already carry prepare_dummy_run EMAIL / PASSWORD keys.
    """
    from field_map import EMAIL, PASSWORD, PASSWORD_CONFIRM

    report: dict[str, Any] = {
        "ran": False,
        "is_login": False,
        "filled": [],
        "clicks": [],
        "blocker": None,
        "reached_app_fields": False,
        "rounds": 0,
        "fill_url": "",
        "final_url": "",
    }

    email = str(values.get(EMAIL) or "").strip()
    password = str(values.get(PASSWORD) or values.get(PASSWORD_CONFIRM) or "").strip()
    if not email:
        report["skipped"] = "no_dummy_email"
        return report

    target = fill_target
    if target is None:
        ctx = await pick_fill_context(page)
        target = ctx.get("frame") or page

    try:
        url = target.url or ""
    except Exception:
        url = ""
    report["fill_url"] = url[:200]

    counts = await _count_auth_inputs(target)
    is_login = looks_like_login_context(
        url,
        password_count=counts.get("password", 0),
        email_count=counts.get("email", 0),
    )
    if not is_login and counts.get("email", 0) >= 1 and counts.get("appish", 0) == 0:
        if _LOGIN_URL_HINT.search(url) or counts.get("password", 0) >= 1:
            is_login = True
    report["is_login"] = is_login
    if not is_login:
        report["skipped"] = "not_login_context"
        return report

    report["ran"] = True
    host_page = page
    try:
        if hasattr(target, "page") and target.page is not None:
            host_page = target.page
    except Exception:
        pass

    for round_i in range(max_rounds):
        report["rounds"] = round_i + 1
        ctx = await pick_fill_context(page)
        target = ctx.get("frame") or target or page
        try:
            url = target.url or ""
        except Exception:
            url = ""
        report["final_url"] = url[:200]

        body = await _frame_body_snip(target)
        title = ""
        try:
            title = await host_page.title()
        except Exception:
            pass
        # Email-verify text stop (before wasting a click)
        text_blocker = detect_auth_blocker(body, title, url)
        if text_blocker == "email_verify":
            report["blocker"] = "email_verify"
            return report

        counts = await _count_auth_inputs(target)
        if counts.get("appish", 0) >= 2 and counts.get("password", 0) == 0:
            report["reached_app_fields"] = True
            report["fill_target"] = target
            return report

        email_done = any(
            f.get("type") == EMAIL and f.get("ok") for f in report["filled"]
        )
        if not email_done and counts.get("email", 0) >= 1:
            got = await _fill_visible(target, _AUTH_EMAIL_SELS, email)
            if got:
                report["filled"].append({**got, "type": EMAIL, "via": "iframe_login"})

        if password and counts.get("password", 0) >= 1:
            report["filled"] = [
                f
                for f in report["filled"]
                if f.get("type") not in (PASSWORD, PASSWORD_CONFIRM)
            ]
            report["filled"].extend(await _fill_passwords(target, password))

        # Visible interactive CAPTCHA → stop (never solve). Still keep filled email.
        if await visible_captcha_challenge(target) or await visible_captcha_challenge(
            host_page
        ):
            report["blocker"] = "captcha"
            report["fill_target"] = target
            return report
        if text_blocker == "captcha":
            report["blocker"] = "captcha"
            report["fill_target"] = target
            return report

        ctrl = await _pick_auth_advance(target)
        if not ctrl:
            body = await _frame_body_snip(target)
            blocker = detect_auth_blocker(body, title, url)
            if await visible_captcha_challenge(target):
                blocker = "captcha"
            if blocker:
                report["blocker"] = blocker
            counts = await _count_auth_inputs(target)
            report["reached_app_fields"] = counts.get("appish", 0) >= 2
            report["fill_target"] = target
            return report

        ok = await _gated_auth_click(target, host_page, ctrl)
        report["clicks"].append(
            {
                "text": ctrl.get("text"),
                "kind": ctrl.get("kind"),
                "ok": bool(ok),
                "round": round_i,
            }
        )
        if not ok:
            return report

        try:
            await host_page.wait_for_timeout(2200)
            await host_page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

        spa = await wait_for_form_spa(
            host_page,
            timeout_ms=12000,
            poll_ms=800,
            clicked_apply=True,
        )
        if spa.get("fill_target") is not None:
            target = spa["fill_target"]
        report["spa_wait"] = {
            "reached": spa.get("reached"),
            "waited_ms": spa.get("waited_ms"),
            "context": spa.get("context"),
        }

        body = await _frame_body_snip(target)
        if await visible_captcha_challenge(target) or await visible_captcha_challenge(
            host_page
        ):
            report["blocker"] = "captcha"
            report["fill_target"] = target
            return report
        blocker = detect_auth_blocker(
            body, title, (getattr(target, "url", None) or url)
        )
        if blocker:
            report["blocker"] = blocker
            return report

        counts = await _count_auth_inputs(target)
        if counts.get("appish", 0) >= 2 and counts.get("password", 0) == 0:
            report["reached_app_fields"] = True
            report["fill_target"] = target
            return report
        if counts.get("password", 0) == 0 and counts.get("email", 0) == 0:
            break

    counts = await _count_auth_inputs(target)
    report["reached_app_fields"] = counts.get("appish", 0) >= 2
    report["fill_target"] = target
    try:
        report["final_url"] = (target.url or "")[:200]
    except Exception:
        pass
    return report
