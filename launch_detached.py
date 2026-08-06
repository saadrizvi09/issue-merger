"""
Launch run_cm_loop.py as a fully detached Windows process that survives
parent process death and harness kills. Writes stdout/stderr to cm_loop.log.
If already running (PID in cm_loop.pid and process alive), does nothing.
"""
import subprocess, sys, os
from pathlib import Path

PROJECT = Path("C:/Projects/Automate pdf merge journal")
PID_FILE = PROJECT / "cm_loop.pid"
LOG_FILE = PROJECT / "cm_loop.log"


def is_running(pid):
    try:
        import ctypes
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle == 0:
            return False
        # WaitForSingleObject with timeout=0: returns 0 if object is signaled (process exited)
        result = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
        ctypes.windll.kernel32.CloseHandle(handle)
        return result != 0  # WAIT_TIMEOUT (258) = still running
    except Exception:
        return False


def main():
    # Check if already running
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if is_running(pid):
                print(f"Already running as PID {pid}. Tail: {LOG_FILE}")
                return
            else:
                print(f"PID {pid} is dead — relaunching.")
        except Exception:
            pass

    # Open log file for append (so we keep history)
    log_fh = open(str(LOG_FILE), "a", encoding="utf-8", buffering=1)

    proc = subprocess.Popen(
        [sys.executable, "-u", str(PROJECT / "run_cm_loop.py")],
        cwd=str(PROJECT),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
    )
    PID_FILE.write_text(str(proc.pid))
    print(f"Launched detached PID {proc.pid} — log: {LOG_FILE}")


if __name__ == "__main__":
    main()
