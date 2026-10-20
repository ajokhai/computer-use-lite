"""Enterprise crash reporting and persistent diagnostics logger for Computer Use.
Saves all application logs, warnings, voice sessions, and unhandled thread exceptions
to ~/Library/Logs/Computer Use/ so users and developers can always inspect what happened.
"""

import datetime
import os
import sys
import threading
import traceback
from pathlib import Path

LOG_DIR = Path.home() / "Library" / "Logs" / "Computer Use"
APP_LOG = LOG_DIR / "app.log"
CRASH_LOG = LOG_DIR / "crash.log"

_INITIALIZED = False
_LOCK = threading.Lock()


class _TeeStream:
    """Tees stdout/stderr to both original console and persistent log file."""

    def __init__(self, original_stream, log_file_path):
        self.orig = original_stream
        self.path = log_file_path

    def write(self, data):
        try:
            if self.orig:
                self.orig.write(data)
                self.orig.flush()
        except Exception:
            pass

        try:
            if data:
                with _LOCK:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(data)
                        f.flush()
        except Exception:
            pass

    def flush(self):
        try:
            if self.orig:
                self.orig.flush()
        except Exception:
            pass


def _uncaught_sys_exception(exc_type, exc_value, exc_traceback):
    """Logs uncaught global exceptions to crash.log with instant fsync."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    record_crash("Uncaught Main Thread Exception", (exc_type, exc_value, exc_traceback))
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


def _uncaught_thread_exception(args):
    """Logs uncaught background thread exceptions to crash.log."""
    thread_name = getattr(args.thread, "name", "UnknownThread")
    record_crash(f"Uncaught Exception in Thread [{thread_name}]", (args.exc_type, args.exc_value, args.exc_traceback))


def record_crash(title: str, exc_info=None):
    """Write an explicit crash report to ~/Library/Logs/Computer Use/crash.log."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        divider = "=" * 75

        lines = [
            f"\n{divider}",
            f"CRASH REPORT - {timestamp}",
            f"Title: {title}",
            f"Python: {sys.version.split()[0]} | Platform: {sys.platform}",
            f"Active Thread: {threading.current_thread().name}",
            divider,
        ]

        if exc_info:
            formatted_tb = "".join(traceback.format_exception(*exc_info))
            lines.append(formatted_tb.rstrip())
        else:
            lines.append("No traceback provided.")
            lines.append("".join(traceback.format_stack()).rstrip())

        lines.append(divider + "\n")
        report = "\n".join(lines)

        with _LOCK:
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(report)
                f.flush()
                os.fsync(f.fileno())

            # Also mirror into app.log
            with open(APP_LOG, "a", encoding="utf-8") as f:
                f.write(report)
                f.flush()
    except Exception as e:
        print(f"Failed to record crash to {CRASH_LOG}: {e}", file=sys.__stderr__)


def setup_logging():
    """Initialize persistent logging, exception hooks, and output teeing."""
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        # Install exception hooks
        sys.excepthook = _uncaught_sys_exception
        if hasattr(threading, "excepthook"):
            threading.excepthook = _uncaught_thread_exception

        # Tee stdout and stderr to app.log
        sys.stdout = _TeeStream(sys.__stdout__, APP_LOG)
        sys.stderr = _TeeStream(sys.__stderr__, APP_LOG)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(APP_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n--- Computer Use Session Started: {timestamp} ---\n")
    except Exception as e:
        print(f"Warning: Failed to setup persistent logging: {e}", file=sys.__stderr__)
