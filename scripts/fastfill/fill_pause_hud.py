#!/usr/bin/env python3
"""Native macOS floating HUD for fill Pause / Continue (outside the browser).

Reads/writes ``.fill_pause_state.json`` beside real_job_results sentinels.
Started as a subprocess by ``fill_pause.start_native_hud``; never injects DOM.

When ``FASTFILL_HUD_PIN_CHROME`` is enabled (default), pins to the fill Chrome
window top-right and follows move/resize. Draggable — offset saved in state.
"""

from __future__ import annotations

import json
import os
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from hud_chrome_bounds import (
    BoundsCache,
    chrome_window_bounds,
    compute_hud_xy,
    default_hud_margins,
    margins_from_hud_xy,
    pin_chrome_enabled,
)

NEVER_SUBMIT = "Never auto-submit — review only"

SYM_PAUSE = "❚❚"
SYM_PLAY = "▶"

# Black theme
BG = "#0a0a0a"
BG_ACCENT = "#141414"
FG = "#e5e5e5"
FG_MUTED = "#a3a3a3"
FG_HINT = "#737373"
BORDER = "#262626"
BTN_AMBER = "#b45309"
BTN_AMBER_ACTIVE = "#92400e"

POLL_MS = 150
LOG_MAX = 50


def _control_path(state_path: Path) -> Path:
    return state_path.parent / ".fill_pause_control.json"


def _pause_sentinel_path(state_path: Path) -> Path:
    return state_path.parent / ".fill_paused"


def _flock_json_update(path: Path, updater) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        if fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
        fh.seek(0)
        raw = fh.read()
        try:
            data = json.loads(raw) if raw.strip() else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        updater(data)
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(data, indent=2))
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except Exception:
            pass
        return data


def _load_state(path: Path) -> dict:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _load_merged(state_path: Path) -> dict:
    """Activity from state file + Pause flags from the control file."""
    st = _load_state(state_path)
    ctrl = _load_state(_control_path(state_path))
    merged = dict(st)
    merged.update(ctrl)
    return merged


def _save_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _save_control(state_path: Path, ctrl: dict) -> None:
    def _upd(data: dict) -> None:
        data.update(ctrl)
        data["updated_at"] = time.time()

    try:
        _flock_json_update(_control_path(state_path), _upd)
    except Exception:
        pass


def _write_pause_sentinel(state_path: Path, paused: bool) -> None:
    path = _pause_sentinel_path(state_path)
    try:
        if paused:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("paused\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except Exception:
        pass


def _hud_margins_from_state(st: dict) -> tuple[int, int]:
    """Read saved top-right margins (margin_right, margin_top)."""
    if "hud_margin_right" in st or "hud_margin_top" in st:
        try:
            return (
                max(0, int(st.get("hud_margin_right") or 0)),
                max(0, int(st.get("hud_margin_top") or 0)),
            )
        except (TypeError, ValueError):
            pass
    # Legacy offset keys (offset from chrome top-left) — convert when possible
    return default_hud_margins()


class FillPauseHUD:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.root = tk.Tk()
        self.root.title("Job Hunter Fill")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        try:
            self.root.wm_attributes("-type", "utility")
        except Exception:
            pass
        pad = {"padx": 10, "pady": 6}
        self.title_font = tkfont.Font(family="Helvetica", size=11, weight="bold")
        self.status_font = tkfont.Font(family="Menlo", size=10)
        self.hint_font = tkfont.Font(family="Helvetica", size=9)
        self.frame = tk.Frame(
            self.root,
            bg=BG,
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.frame.pack(fill="both", expand=True)
        self.btn = tk.Button(
            self.frame,
            text=f"{SYM_PAUSE}  Pause fill",
            font=self.title_font,
            fg=FG,
            bg=BG,
            activebackground=BG_ACCENT,
            activeforeground=FG,
            relief="flat",
            cursor="hand2",
            command=self._on_click,
        )
        self.btn.pack(fill="x", **pad)
        self.status = tk.Label(
            self.frame,
            text="— · idle",
            font=self.status_font,
            fg=FG,
            bg=BG,
            anchor="w",
            justify="left",
            wraplength=280,
        )
        self.status.pack(fill="x", **pad)
        self.hint = tk.Label(
            self.frame,
            text=NEVER_SUBMIT,
            font=self.hint_font,
            fg=FG_MUTED,
            bg=BG,
            anchor="w",
            justify="left",
            wraplength=280,
        )
        self.hint.pack(fill="x", **pad)
        self.detail = tk.Label(
            self.frame,
            text="Pause stops filling immediately. Continue resumes. Never submit.",
            font=self.hint_font,
            fg=FG_HINT,
            bg=BG,
            anchor="w",
            justify="left",
            wraplength=300,
        )
        self.detail.pack(fill="x", **pad)
        self.log = tk.Text(
            self.frame,
            height=8,
            width=42,
            font=self.status_font,
            fg=FG,
            bg=BG_ACCENT,
            wrap="word",
            state="disabled",
            highlightthickness=0,
            bd=0,
            padx=4,
            pady=4,
        )
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self._bounds_cache = BoundsCache()
        self._dragging = False
        self._drag_origin_x = 0
        self._drag_origin_y = 0
        self._drag_hud_x = 0
        self._drag_hud_y = 0
        self._chrome_missing_ticks = 0
        self._hidden_until_pin = False
        for widget in (self.frame, self.btn, self.status, self.hint, self.detail):
            widget.bind("<ButtonPress-1>", self._drag_start, add="+")
            widget.bind("<B1-Motion>", self._drag_motion, add="+")
            widget.bind("<ButtonRelease-1>", self._drag_end, add="+")
        self._place_initial()
        self.root.after(POLL_MS, self._poll)

    def _hud_size(self) -> tuple[int, int]:
        self.root.update_idletasks()
        return (self.root.winfo_width() or 300, self.root.winfo_height() or 120)

    def _place_screen_top_right(self) -> None:
        if self._hidden_until_pin:
            try:
                self.root.deiconify()
                self.root.attributes("-topmost", True)
            except Exception:
                pass
            self._hidden_until_pin = False
        w, _h = self._hud_size()
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"+{max(8, sw - w - 16)}+12")
        self._bounds_cache.clear()

    def _place_initial(self) -> None:
        if pin_chrome_enabled():
            st = _load_merged(self.state_path)
            pid = st.get("fill_chrome_pid")
            if pid and self._reposition_to_chrome(st, force=True):
                return
            # Stay hidden until Chrome PID/bounds appear — don't jump to a random
            # screen corner (fill window may be the right 2/3 of the display).
            self._hidden_until_pin = True
            try:
                self.root.withdraw()
            except Exception:
                pass
            return
        self._place_screen_top_right()

    def _reposition_to_chrome(self, st: dict, *, force: bool = False) -> bool:
        pid = st.get("fill_chrome_pid")
        if not pid:
            return False
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            return False
        chrome = chrome_window_bounds(pid_i)
        if not chrome:
            return False
        w, h = self._hud_size()
        margin_right, margin_top = _hud_margins_from_state(st)
        if not force and not self._bounds_cache.should_reposition(
            chrome,
            hud_width=w,
            hud_height=h,
            margin_right=margin_right,
            margin_top=margin_top,
        ):
            return True
        x, y = compute_hud_xy(
            chrome,
            hud_width=w,
            hud_height=h,
            margin_right=margin_right,
            margin_top=margin_top,
        )
        self.root.geometry(f"+{x}+{y}")
        if self._hidden_until_pin:
            try:
                self.root.deiconify()
                self.root.attributes("-topmost", True)
            except Exception:
                pass
            self._hidden_until_pin = False
        return True

    def _drag_start(self, event: tk.Event) -> None:
        self._dragging = True
        self._drag_origin_x = event.x_root
        self._drag_origin_y = event.y_root
        self._drag_hud_x = self.root.winfo_x()
        self._drag_hud_y = self.root.winfo_y()

    def _drag_motion(self, event: tk.Event) -> None:
        if not self._dragging:
            return
        dx = event.x_root - self._drag_origin_x
        dy = event.y_root - self._drag_origin_y
        self.root.geometry(f"+{self._drag_hud_x + dx}+{self._drag_hud_y + dy}")

    def _drag_end(self, event: tk.Event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        if not pin_chrome_enabled():
            return
        st = _load_merged(self.state_path)
        pid = st.get("fill_chrome_pid")
        if not pid:
            return
        try:
            chrome = chrome_window_bounds(int(pid))
        except (TypeError, ValueError):
            return
        if not chrome:
            return
        w, h = self._hud_size()
        margin_right, margin_top = margins_from_hud_xy(
            chrome,
            hud_x=self.root.winfo_x(),
            hud_y=self.root.winfo_y(),
            hud_width=w,
            hud_height=h,
        )
        activity = _load_state(self.state_path)
        activity["hud_margin_right"] = margin_right
        activity["hud_margin_top"] = margin_top
        activity["updated_at"] = time.time()
        _save_state(self.state_path, activity)
        self._bounds_cache.clear()

    def _maybe_reposition(self, st: dict) -> None:
        if self._dragging:
            return
        if not pin_chrome_enabled():
            return
        pid = st.get("fill_chrome_pid")
        if not pid:
            self._chrome_missing_ticks = 0
            return
        if self._reposition_to_chrome(st):
            self._chrome_missing_ticks = 0
            return
        self._chrome_missing_ticks += 1
        # Keep retrying pin; only fall back to screen corner after ~5s.
        if self._chrome_missing_ticks >= 30:
            self._place_screen_top_right()

    def _on_click(self) -> None:
        st = _load_merged(self.state_path)
        paused = bool(st.get("paused"))
        captcha = bool(st.get("captcha_gated"))
        hold = bool(st.get("hold_mode"))
        ctrl = {
            "paused": st.get("paused"),
            "hold_mode": st.get("hold_mode"),
            "captcha_gated": st.get("captcha_gated"),
            "pause_count": int(st.get("pause_count") or 0),
            "continue_count": int(st.get("continue_count") or 0),
            "hud_action": st.get("hud_action"),
        }
        if captcha or hold:
            ctrl["paused"] = False
            ctrl["hold_mode"] = False
            ctrl["continue_count"] = int(ctrl.get("continue_count") or 0) + 1
            ctrl["hud_action"] = "continue"
        else:
            ctrl["paused"] = not paused
            if ctrl["paused"]:
                ctrl["pause_count"] = int(ctrl.get("pause_count") or 0) + 1
                ctrl["hud_action"] = "pause"
            else:
                ctrl["continue_count"] = int(ctrl.get("continue_count") or 0) + 1
                ctrl["hud_action"] = "continue"
        _save_control(self.state_path, ctrl)
        _write_pause_sentinel(self.state_path, bool(ctrl["paused"]))

    def _sync_ui(self, st: dict) -> None:
        compact = str(st.get("compact") or st.get("text") or "— · idle")
        self.status.config(text=compact[:220])
        paused = bool(st.get("paused"))
        captcha = bool(st.get("captcha_gated"))
        hold = bool(st.get("hold_mode"))
        if captcha:
            self.btn.config(
                text=f"{SYM_PLAY}  Continue (CAPTCHA)",
                bg=BTN_AMBER,
                activebackground=BTN_AMBER_ACTIVE,
            )
            self.detail.config(
                text="CAPTCHA — solve in Chrome, then Continue. Never auto-solved."
            )
        elif hold:
            self.btn.config(
                text=f"{SYM_PLAY}  Continue",
                bg=BTN_AMBER,
                activebackground=BTN_AMBER_ACTIVE,
            )
            blob = compact.lower()
            if "incomplete" in blob:
                self.detail.config(
                    text="On hold (incomplete) — Continue resumes fill / Next. Never submit."
                )
            else:
                self.detail.config(
                    text="On hold — Continue resumes fill / Next. Never submit."
                )
        elif paused:
            self.btn.config(
                text=f"{SYM_PLAY}  Continue fill",
                bg=BTN_AMBER,
                activebackground=BTN_AMBER_ACTIVE,
            )
            self.detail.config(
                text="Paused — filling stopped. Edit form in Chrome, then Continue."
            )
        else:
            self.btn.config(
                text=f"{SYM_PAUSE}  Pause fill",
                bg=BG,
                activebackground=BG_ACCENT,
            )
            self.detail.config(
                text="Pause stops filling immediately. Continue resumes. Never submit."
            )
        self._sync_log(st)

    def _sync_log(self, st: dict) -> None:
        rows = st.get("log") if isinstance(st.get("log"), list) else []
        lines: list[str] = []
        for row in rows[-LOG_MAX:]:
            if isinstance(row, dict):
                line = str(row.get("line") or "").strip()
            else:
                line = str(row or "").strip()
            if line:
                lines.append(line)
        text = "\n".join(lines) if lines else "(waiting for fill activity)"
        current = self.log.get("1.0", "end-1c")
        if current == text:
            return
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("1.0", text)
        self.log.see("end")
        self.log.config(state="disabled")

    def _poll(self) -> None:
        if str(_load_state(self.state_path).get("hud_stop")) == "1":
            self.root.destroy()
            return
        st = _load_merged(self.state_path)
        self._sync_ui(st)
        self._maybe_reposition(st)
        self.root.after(POLL_MS, self._poll)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fill_pause_hud.py <state.json>", file=sys.stderr)
        return 2
    hud = FillPauseHUD(Path(sys.argv[1]).expanduser())
    hud.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
