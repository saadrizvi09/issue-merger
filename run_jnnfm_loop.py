"""
Supervisor loop for JNNFM (ScienceDirect) downloads on port 9224 / profile chrome_sd_9224.
Runs jnnfm_download.py batches until all articles are downloaded. Keeps the warm logged-in
Chrome (ScienceDirect has no Radware relaunch need; MapMyAccess session persists in profile).
Relaunches Chrome only if it dies. Detached, no terminal popups.
"""
import subprocess, time, json, re, sys, urllib.request
from pathlib import Path

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"C:\Users\acer\AppData\Local\Temp\chrome_sd_9224"
PORT = 9224
PROJECT = Path("C:/Projects/Automate pdf merge journal")
MANIFEST = "jnnfm_0377-0257_dois.json"
DLDIR = "jnnfm_downloads"
BASE = "https://www-sciencedirect-com.jmi.mapmyaccess.com"
JOURNAL = BASE + "/journal/journal-of-non-newtonian-fluid-mechanics"
BUDGET = 600
DELAY = 5.0    # success rate is server-side probabilistic (~1/3), not delay-sensitive; cycle fast + retry across passes
MIN_YEAR, MAX_YEAR = 2018, 2026
NW = subprocess.CREATE_NO_WINDOW

def autologin():
    """Ensure the ScienceDirect MapMyAccess session is live (idempotent; ~5s when already in)."""
    try:
        r = subprocess.run([sys.executable, "-u", str(PROJECT / "mma_autologin_9224.py")],
                           cwd=str(PROJECT), timeout=140, capture_output=True, creationflags=NW)
        return r.returncode == 0
    except Exception:
        return False

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))

def count_remaining():
    data = json.loads((PROJECT / MANIFEST).read_text(encoding="utf-8"))
    dl = PROJECT / DLDIR
    n = 0
    for k, arts in data["issues"].items():
        for a in arts:
            y = a.get("year", 0)
            if not (MIN_YEAR <= y <= MAX_YEAR): continue
            if not a.get("pii"): continue
            fp = dl / f"V{safe(a.get('volume','?'))}I{safe(a.get('issue','?'))}" / f"{safe(a['doi'])}.pdf"
            if not (fp.exists() and fp.stat().st_size > 10000):
                n += 1
    return n

def chrome_alive():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3); return True
    except Exception:
        return False

def launch_chrome():
    subprocess.Popen([CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}", JOURNAL],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NW)
    for _ in range(20):
        time.sleep(2)
        if chrome_alive():
            print(f"  Chrome {PORT} up", flush=True); return True
    return False

def run_batch():
    cmd = [sys.executable, "-u", str(PROJECT / "jnnfm_download.py"),
           "--manifest", str(PROJECT / MANIFEST), "--dldir", str(PROJECT / DLDIR),
           "--base", BASE, "--cdp", f"http://127.0.0.1:{PORT}",
           "--min-year", str(MIN_YEAR), "--max-year", str(MAX_YEAR),
           "--delay", str(DELAY), "--budget", str(BUDGET)]
    r = subprocess.run(cmd, cwd=str(PROJECT), timeout=BUDGET + 180, capture_output=True, creationflags=NW)
    return r.returncode

(PROJECT / DLDIR).mkdir(exist_ok=True)
batch = 0
while True:
    remaining = count_remaining()
    print(f"\n=== JNNFM Batch {batch+1}: {remaining} remaining ===", flush=True)
    if remaining == 0:
        print("ALL DONE — JNNFM 2018-2026 complete!", flush=True); break
    if not chrome_alive():
        print("  Chrome down, relaunching...", flush=True)
        if not launch_chrome():
            print("  Chrome failed to start, retry 15s...", flush=True); time.sleep(15); continue
    # Ensure a live MMA/ScienceDirect session each batch (auto re-login via cached MS session)
    if autologin():
        print("  session OK", flush=True)
    else:
        print("  auto-login failed — waiting 60s (may need manual Microsoft sign-in on 9224)...", flush=True)
        time.sleep(60); continue
    try:
        rc = run_batch()
    except subprocess.TimeoutExpired:
        print("  Batch timed out; cooldown 10s...", flush=True); time.sleep(10); batch += 1; continue
    except Exception as e:
        print(f"  Batch error: {e}; 10s...", flush=True); time.sleep(10); batch += 1; continue
    time.sleep(8)
    batch += 1
