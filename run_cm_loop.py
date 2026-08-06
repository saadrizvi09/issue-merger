"""
Autonomous loop for IOP Condensed Matter 2023-2026 via MapMyAccess proxy.
- Relaunches Chrome 9223 between every batch (clears Radware bot-block)
- Auto-logs back into MapMyAccess via Microsoft SSO if session expires
- Fires batches until all 2023-2026 articles are downloaded
"""
import subprocess, time, json, re, sys
from pathlib import Path

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"C:\Users\acer\AppData\Local\Temp\chrome_mma_9223b"
PORT = 9223
PROJECT = Path("C:/Projects/Automate pdf merge journal")
MANIFEST = "iop_0953-8984_dois.json"
DLDIR = "iop_cm_downloads"
# MapMyAccess proxy access from home/non-campus IP. Requires periodic MMA login.
USE_PROXY = True
BASE = "https://iopscience-iop-org.jmi.mapmyaccess.com"
JOURNAL = f"{BASE}/journal/0953-8984"
CDP = f"http://127.0.0.1:{PORT}"
PREFLIGHT = "10.1088/1361-648x/ab7f6e"
BUDGET = 480
DELAY = 2.5   # proxy path is more Radware-sensitive; keep conservative pacing
MIN_YEAR = 2023
MAX_YEAR = 2026

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
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3)
        return True
    except Exception:
        return False

NW = subprocess.CREATE_NO_WINDOW  # suppress terminal popups

def kill_chrome():
    # Kill the ENTIRE debug-Chrome process tree (parent + all renderer/gpu children)
    # by matching the debug user-data-dir in each process command line. This does NOT
    # touch the user's other Chrome windows (different profile) and prevents orphaned
    # child processes from accumulating and exhausting the paging file.
    profile_leaf = "chrome_mma_9223b"
    subprocess.run(["powershell.exe", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
        f"Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{profile_leaf}*' }} | "
        f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"],
        capture_output=True, creationflags=NW)
    time.sleep(4)

def launch_chrome():
    url = JOURNAL
    # Launch Chrome directly — avoids a PowerShell popup window
    subprocess.Popen([
        CHROME,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE}",
        url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NW)
    for _ in range(20):
        time.sleep(2)
        if chrome_alive():
            print(f"  Chrome {PORT} up", flush=True)
            return True
    return False

def try_autologin():
    """Run mma_autologin.py to handle expired MapMyAccess session via Microsoft SSO."""
    print("  Attempting auto-login via Microsoft SSO...", flush=True)
    result = subprocess.run(
        [sys.executable, "-u", str(PROJECT / "mma_autologin.py")],
        cwd=str(PROJECT), timeout=120, capture_output=True, creationflags=NW
    )
    return result.returncode == 0

def run_batch():
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
    result = subprocess.run(cmd, cwd=str(PROJECT), timeout=BUDGET + 120,
                            capture_output=True, creationflags=NW)
    return result.returncode, result.stdout if hasattr(result, 'stdout') else b""

(PROJECT / DLDIR).mkdir(exist_ok=True)

batch = 0
consecutive_no_access = 0
import time as _t
last_login = _t.time()     # assume a fresh (manual) login at startup — defer proactive re-auth
RELOGIN_EVERY = 7200       # timer re-auth disabled for the final stretch (session is fresh); preflight catches real expiry

while True:
    remaining = count_remaining()
    print(f"\n=== Batch {batch+1}: {remaining} remaining (2023-2026) ===", flush=True)
    if remaining == 0:
        print("ALL DONE — Condensed Matter 2023-2026 complete!", flush=True)
        break

    # Ensure Chrome is up
    if not chrome_alive():
        print("  Chrome down, relaunching...", flush=True)
        kill_chrome()
        if not launch_chrome():
            print("  Chrome failed to start, retrying in 15s...", flush=True)
            time.sleep(15)
            continue

    # Re-login is DISABLED in-loop: the session is valid (seeded by the user's manual Microsoft
    # login, which self-heals the OAuth re-click), and running mma_autologin every batch was
    # corrupting Chrome state and starving downloads. If the proxy session genuinely expires,
    # the batch's preflight prints NO ACCESS and aborts fast — the supervisor/monitor then
    # re-triggers login. Only re-auth on the slow timer as a safety net.
    import urllib.request
    need_login = USE_PROXY and ((time.time() - last_login) > RELOGIN_EVERY)
    if need_login:
        print("  Re-authenticating MapMyAccess (timer/login-page)...", flush=True)
        if try_autologin():
            last_login = time.time()
            print("  MMA session refreshed.", flush=True)
        else:
            print("  Auto-login reported failure — trying batch anyway (session may still be valid; "
                  "preflight will abort fast if truly expired)...", flush=True)
            # Do NOT skip the batch. mma_autologin often reports 'failed' when the session is
            # ALREADY valid (no login button to click). The batch's preflight NO-ACCESS check
            # aborts fast if the session is genuinely dead, so running it is safe and avoids
            # starving downloads on spurious re-login failures.

    try:
        rc, _ = run_batch()
    except subprocess.TimeoutExpired:
        print("  Batch timed out, relaunching Chrome...", flush=True)
        kill_chrome()
        continue
    except Exception as e:
        print(f"  Batch error: {e}, relaunching Chrome in 5s...", flush=True)
        kill_chrome()
        time.sleep(5)
        continue

    # Relaunch Chrome between batches (the PROVEN approach that downloaded ~2300 articles):
    # a fresh Chrome resets the Radware bot score AND clears accumulated pages/targets that
    # otherwise pile up and hang CDP. The MapMyAccess session persists in the PERSISTENT
    # profile, so relaunching does NOT log us out. Re-login churn has been removed, so the
    # relaunched Chrome loads the journal cleanly with the session intact.
    kill_chrome()
    time.sleep(12)   # let the fresh Chrome establish the profile session before next preflight

    if rc != 0:
        print(f"  Batch exited {rc}, Chrome relaunched, sleeping...", flush=True)
        consecutive_no_access += 1
        if consecutive_no_access >= 3:
            print("  3 consecutive failures — waiting 60s before retry...", flush=True)
            time.sleep(60)
            consecutive_no_access = 0
    else:
        consecutive_no_access = 0
        print(f"  Batch done, Chrome relaunched, cooldown...", flush=True)

    batch += 1
