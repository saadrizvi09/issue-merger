"""Launch run_jams_loop.py as a detached Windows process (survives terminal/harness kills)."""
import subprocess, sys
from pathlib import Path

PROJECT = Path("C:/Projects/Automate pdf merge journal")
PID_FILE = PROJECT / "jams_loop.pid"
LOG_FILE = PROJECT / "jams_loop.log"

def is_running(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not h: return False
        r = ctypes.windll.kernel32.WaitForSingleObject(h, 0)
        ctypes.windll.kernel32.CloseHandle(h)
        return r != 0
    except Exception:
        return False

def main():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if is_running(pid):
                print(f"Already running as PID {pid}."); return
            print(f"PID {pid} dead — relaunching.")
        except Exception:
            pass
    log = open(str(LOG_FILE), "a", encoding="utf-8", buffering=1)
    proc = subprocess.Popen([sys.executable, "-u", str(PROJECT / "run_jams_loop.py")],
                            cwd=str(PROJECT), stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)
    PID_FILE.write_text(str(proc.pid))
    print(f"Launched detached JAMS loop PID {proc.pid} — log: {LOG_FILE}")

if __name__ == "__main__":
    main()
