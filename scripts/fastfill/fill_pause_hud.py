#!/usr/bin/env python3
"""Native macOS floating HUD for fill Pause / Continue (outside the browser).

Reads/writes ``.fill_pause_state.json`` beside real_job_results sentinels.
Started as a subprocess by ``fill_pause.start_native_hud``; never injects DOM.
"""

from __future__ import annotations

import json
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

NEVER_SUBMIT = "Never auto-submit — review only"

SYM_PAUSE = "❚❚"
SYM_PLAY = "▶"


def _load_state(path: Path) -> dict:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


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
        frame = tk.Frame(self.root, bg="#0f172a", bd=0, highlightthickness=1, highlightbackground="#334155")
        frame.pack(fill="both", expand=True)
        self.btn = tk.Button(
            frame,
            text=f"{SYM_PAUSE}  Pause fill",
            font=self.title_font,
            fg="#f8fafc",
            bg="#0f172a",
            activebackground="#1e293b",
            activeforeground="#f8fafc",
            relief="flat",
            cursor="hand2",
            command=self._on_click,
        )
        self.btn.pack(fill="x", **pad)
        self.status = tk.Label(
            frame,
            text="— · idle",
            font=self.status_font,
            fg="#e2e8f0",
            bg="#0f172a",
            anchor="w",
            justify="left",
            wraplength=280,
        )
        self.status.pack(fill="x", **pad)
        self.hint = tk.Label(
            frame,
            text=NEVER_SUBMIT,
            font=self.hint_font,
            fg="#94a3b8",
            bg="#0f172a",
            anchor="w",
            justify="left",
            wraplength=280,
        )
        self.hint.pack(fill="x", **pad)
        self.detail = tk.Label(
            frame,
            text="Pause takes effect between fill actions (not mid-widget).",
            font=self.hint_font,
            fg="#64748b",
            bg="#0f172a",
            anchor="w",
            justify="left",
            wraplength=280,
        )
        self.detail.pack(fill="x", **pad)
        self._place_top_right()
        self.root.after(200, self._poll)

    def _place_top_right(self) -> None:
        self.root.update_idletasks()
        w = self.root.winfo_width() or 300
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"+{max(8, sw - w - 16)}+12")

    def _on_click(self) -> None:
        st = _load_state(self.state_path)
        paused = bool(st.get("paused"))
        captcha = bool(st.get("captcha_gated"))
        hold = bool(st.get("hold_mode"))
        if captcha or hold:
            # Continue semantics
            st["paused"] = False
            st["hold_mode"] = False
            st["continue_count"] = int(st.get("continue_count") or 0) + 1
            st["hud_action"] = "continue"
        else:
            st["paused"] = not paused
            if st["paused"]:
                st["pause_count"] = int(st.get("pause_count") or 0) + 1
                st["hud_action"] = "pause"
            else:
                st["continue_count"] = int(st.get("continue_count") or 0) + 1
                st["hud_action"] = "continue"
        st["updated_at"] = time.time()
        _save_state(self.state_path, st)

    def _sync_ui(self, st: dict) -> None:
        compact = str(st.get("compact") or st.get("text") or "— · idle")
        self.status.config(text=compact[:220])
        paused = bool(st.get("paused"))
        captcha = bool(st.get("captcha_gated"))
        hold = bool(st.get("hold_mode"))
        if captcha:
            self.btn.config(
                text=f"{SYM_PLAY}  Continue (CAPTCHA)",
                bg="#b45309",
            )
            self.detail.config(
                text="CAPTCHA — solve in Chrome, then Continue. Never auto-solved."
            )
        elif hold:
            self.btn.config(
                text=f"{SYM_PLAY}  Continue",
                bg="#b45309",
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
                bg="#b45309",
            )
            self.detail.config(
                text="Paused between actions — edit form in Chrome, then Continue."
            )
        else:
            self.btn.config(
                text=f"{SYM_PAUSE}  Pause fill",
                bg="#0f172a",
            )
            self.detail.config(
                text="Pause takes effect between fill actions (not mid-widget)."
            )

    def _poll(self) -> None:
        if str(_load_state(self.state_path).get("hud_stop")) == "1":
            self.root.destroy()
            return
        st = _load_state(self.state_path)
        self._sync_ui(st)
        self.root.after(200, self._poll)

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
