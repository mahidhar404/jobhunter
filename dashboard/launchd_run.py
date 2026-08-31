#!/usr/bin/env python3
"""launchd entrypoint for the dashboard (com.jobhunter.dashboard).

launchd itself cannot open files inside TCC-protected folders (~/Desktop) and
/bin/bash is TCC-denied there too, but the venv python binary is allowed. So
the plist execs THIS file with StandardOutPath=/tmp, we re-point stdout/stderr
at the repo log, then exec server.py in-place (fds survive execv).
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
(ROOT / "logs").mkdir(exist_ok=True)

log_fd = os.open(
    str(ROOT / "logs" / "dashboard_server.out"),
    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
    0o644,
)
os.dup2(log_fd, 1)
os.dup2(log_fd, 2)
os.close(log_fd)
print(
    f"===== launchd spawn {time.strftime('%Y-%m-%dT%H:%M:%S%z')} (pid {os.getpid()}) =====",
    flush=True,
)

os.execv(sys.executable, [sys.executable, "-u", str(ROOT / "dashboard" / "server.py")])
