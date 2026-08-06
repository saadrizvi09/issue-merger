"""
Run exactly ONE download batch then exit.
Designed to complete within 110s so it fits in a 2-min foreground Bash call.
Usage: python run_one_batch.py [budget_seconds]
"""
import subprocess, time, sys, json, re, urllib.request
from pathlib import Path

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"C:\Users\acer\AppData\Local\Temp\chrome_mma_9223b"
PORT = 9223
PROJECT = Path("C:/Projects/Automate pdf merge journal")
MANIFEST = "iop_0953-8984_dois.json"
DLDIR = "iop_cm_downloads"
BASE = "https://iopscience-iop-org.jmi.mapmyaccess.com"
CDP = f"http://127.0.0.1:{PORT}"
PREFLIGHT = "10.1088/1361-648x/ab7f6e"
DELAY = 2.5
MIN_YEAR = 2023
MAX_YEAR = 2026

BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 75

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))

def count_remaining():
    data = json.loads((PROJECT / MANIFEST).read_text(encoding="utf-8"))
    dldir = PROJECT / DLDIR
    n = 0
    for k, arts in data["issues"].items():
        for a in arts:
            y = a.get("year", 0)
            if not (MIN_YEAR <= y <= MAX_YEAR): continue
            v = a.get("volume", "?"); iss = a.get("issue", "?")
            fp = dldir / f"V{safe(v)}I{safe(iss)}" / f"{safe(a['doi'])}.pdf"
            if not (fp.exists() and fp.stat().st_size > 10000):
                n += 1
    return n

def chrome_alive():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3)
        return True
    except Exception:
        return False

def kill_chrome():
    # Kill only the Chrome instance on port 9223, not all Chrome windows
    subprocess.run(["powershell.exe", "-Command",
        f"$p=(netstat -ano | Select-String ':{PORT}.*LISTENING'); if($p){{$pid_=($p -split '\\s+')[-1]; Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue}}"],
        capture_output=True)
    time.sleep(4)

def launch_chrome():
    url = f"https://iopscience-iop-org.jmi.mapmyaccess.com/journal/0953-8984"
    subprocess.Popen([
        "powershell.exe", "-Command",
        f"Start-Process '{CHROME}' -ArgumentList '--remote-debugging-port={PORT} --user-data-dir={PROFILE} {url}'"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(2)
        if chrome_alive():
            print(f"Chrome {PORT} up", flush=True)
            return True
    return False

def try_autologin():
    print("Auto-login via OAuth...", flush=True)
    result = subprocess.run(
        [sys.executable, "-u", str(PROJECT / "mma_autologin.py")],
        cwd=str(PROJECT), timeout=90, capture_output=False
    )
    return result.returncode == 0

# ── main ──────────────────────────────────────────────────────────────────────
remaining = count_remaining()
print(f"Remaining: {remaining}/2415", flush=True)
if remaining == 0:
    print("ALL DONE", flush=True)
    sys.exit(0)

# Always kill+relaunch to reset Radware state
kill_chrome()
if not launch_chrome():
    print("Chrome failed to start", flush=True)
    sys.exit(1)

# Check for login page in tabs
try:
    tabs_json = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5).read().decode()
    if 'Login | MMA' in tabs_json or 'MMA | Jamia' in tabs_json:
        if not try_autologin():
            print("Auto-login failed — retrying once...", flush=True)
            time.sleep(10)
            try_autologin()
except Exception:
    pass

# Run one batch
cmd = [
    sys.executable, "-u", str(PROJECT / "iop_download.py"),
    "--manifest", str(PROJECT / MANIFEST),
    "--dldir", str(PROJECT / DLDIR),
    "--base", BASE,
    "--cdp", CDP,
    "--preflight-doi", PREFLIGHT,
    "--min-year", str(MIN_YEAR),
    "--max-year", str(MAX_YEAR),
    "--conc", "1",
    "--delay", str(DELAY),
    "--budget", str(BUDGET),
]
result = subprocess.run(cmd, cwd=str(PROJECT), timeout=BUDGET + 60)
rc = result.returncode

kill_chrome()

remaining_after = count_remaining()
print(f"Batch done (rc={rc}). Remaining: {remaining_after}/2415", flush=True)
sys.exit(0 if remaining_after == 0 else 2)  # exit 2 = more work to do
