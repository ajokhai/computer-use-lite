"""Keep the Mac (and display) awake while a task runs, via the built-in caffeinate."""

import os
import subprocess
from contextlib import contextmanager


@contextmanager
def stay_awake(enabled: bool = True):
    if not enabled:
        yield
        return
    # -w ties it to our pid, so it also ends if we crash.
    proc = subprocess.Popen(["caffeinate", "-dimsu", "-w", str(os.getpid())])
    try:
        yield
    finally:
        proc.terminate()
