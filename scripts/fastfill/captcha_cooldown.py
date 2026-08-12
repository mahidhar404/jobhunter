#!/usr/bin/env python3
"""Bot-pressure CAPTCHA burst cooldown for the improvement cycle.

Repeated CAPTCHA/Cloudflare means detection is heating up — never Fixer-retry,
never solve; after a burst, sleep then continue. Dummy-only orchestration.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


@dataclass
class CaptchaCooldownState:
    """Rolling window of CAPTCHA/BLOCKED timestamps (epoch seconds)."""

    hits: list[float] = field(default_factory=list)
    escalations: int = 0
    last_cooldown_s: float = 0.0
    burst_n: int = field(default_factory=lambda: _env_int("CAPTCHA_BURST_N", 3))
    window_attempts: int = field(
        default_factory=lambda: _env_int("CAPTCHA_BURST_WINDOW", 8)
    )
    window_s: float = field(
        default_factory=lambda: float(_env_int("CAPTCHA_BURST_WINDOW_S", 1800))
    )
    cooldown_s: float = field(
        default_factory=lambda: float(_env_int("CAPTCHA_COOLDOWN_S", 180))
    )
    escalate_s: float = field(
        default_factory=lambda: float(_env_int("CAPTCHA_COOLDOWN_ESCALATE_S", 600))
    )
    max_escalations: int = field(
        default_factory=lambda: _env_int("CAPTCHA_MAX_ESCALATIONS", 2)
    )

    def _prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        # Keep hits within time window; also cap list length to window_attempts*2
        self.hits = [t for t in self.hits if now - t <= self.window_s]
        if len(self.hits) > self.window_attempts * 2:
            self.hits = self.hits[-self.window_attempts * 2 :]

    def record_blocked(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.hits.append(now)
        self._prune(now)

    def hits_in_window(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        self._prune(now)
        # Prefer attempt-window semantics: last N hits that still fall in time window
        recent = [t for t in self.hits if now - t <= self.window_s]
        return min(len(recent), len(self.hits))

    def should_cooldown(self, *, now: float | None = None) -> bool:
        return self.hits_in_window(now=now) >= self.burst_n

    def next_action(self, *, now: float | None = None) -> dict[str, Any]:
        """After a BLOCKED hit was recorded, decide continue / cooldown / pause.

        Returns decision dict: {action, sleep_s, reason, escalations, hits}.
        action ∈ continue | cooldown | pause_bot_pressure
        """
        now = time.time() if now is None else now
        hits = self.hits_in_window(now=now)
        if hits < self.burst_n:
            return {
                "action": "continue",
                "sleep_s": 0,
                "reason": "below_burst",
                "escalations": self.escalations,
                "hits": hits,
            }
        # Burst threshold met
        if self.escalations >= self.max_escalations:
            return {
                "action": "pause_bot_pressure",
                "sleep_s": 0,
                "reason": "bot_pressure_max_escalations",
                "escalations": self.escalations,
                "hits": hits,
            }
        sleep = self.cooldown_s if self.escalations == 0 else self.escalate_s
        # Clamp 120–600 for first; escalate may be higher but cap 900
        if self.escalations == 0:
            sleep = max(120.0, min(600.0, float(sleep)))
        else:
            sleep = max(300.0, min(900.0, float(sleep)))
        return {
            "action": "cooldown",
            "sleep_s": sleep,
            "reason": "bot_pressure" if self.escalations == 0 else "bot_pressure_escalated",
            "escalations": self.escalations,
            "hits": hits,
        }

    def mark_cooldown_done(self) -> None:
        self.escalations += 1
        self.last_cooldown_s = time.time()
        # Clear hits so the next window starts fresh after resting
        self.hits.clear()


def sleep_cooldown(seconds: float, *, sleeper=time.sleep) -> None:
    """Sleep without launching browsers. Testable via sleeper inject."""
    s = max(0.0, float(seconds))
    if s > 0:
        sleeper(s)


def _self_test() -> None:
    st = CaptchaCooldownState(burst_n=3, window_attempts=8, window_s=1800, cooldown_s=180)
    now = 1_000_000.0
    st.record_blocked(now=now)
    st.record_blocked(now=now + 1)
    assert st.next_action(now=now + 2)["action"] == "continue"
    st.record_blocked(now=now + 2)
    act = st.next_action(now=now + 3)
    assert act["action"] == "cooldown" and act["sleep_s"] >= 120, act
    st.mark_cooldown_done()
    assert st.escalations == 1 and st.hits == []
    # Second burst → escalate sleep
    for i in range(3):
        st.record_blocked(now=now + 100 + i)
    act2 = st.next_action(now=now + 110)
    assert act2["action"] == "cooldown" and act2["sleep_s"] >= 300, act2
    st.mark_cooldown_done()
    for i in range(3):
        st.record_blocked(now=now + 200 + i)
    act3 = st.next_action(now=now + 210)
    assert act3["action"] == "pause_bot_pressure", act3
    slept = {"n": 0}

    def fake_sleep(s):
        slept["n"] += s

    sleep_cooldown(3, sleeper=fake_sleep)
    assert slept["n"] == 3
    print("captcha_cooldown self-test OK")


if __name__ == "__main__":
    _self_test()
