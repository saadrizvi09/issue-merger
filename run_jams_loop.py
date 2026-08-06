"""Supervisor loop for free-archive JAMS (2018-2020) on port 9224. Loops jams_download.py
until all 68 are downloaded. Relaunches the 9224 Chrome if it dies/hangs. Detached, no popups."""
import subprocess, time, json, re, sys, urllib.request
from pathlib import Path

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"C:\Users\acer\AppData\Local\Temp\chrome_sd_9224"
PORT = 9224
PROJECT = Path("C:/Projects/Automate pdf merge journal")
MANIFEST = "jams_0894-0347_dois.json"
DLDIR = "jams_downloads"
DELAY = 4.0
BUDGET = 600
NW = subprocess.CREATE_NO_WINDOW

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))

def count_remaining():
    data = json.loads((PROJECT / MANIFEST).read_text(encoding="utf-8"))
    dl = PROJECT / DLDIR
    n = 0
    for k, arts in data["issues"].items():
        for a in arts:
            fp = dl / f"V{safe(a['volume'])}I{safe(a['issue'])}" / f"{safe(a['doi'])}.pdf"
            if not (fp.exists() and fp.stat().st_size > 10000):
                n += 1
    return n

def chrome_alive():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3); return True
    except Exception:
        return False

def kill_chrome():
    subprocess.run(["powershell.exe", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
        f"Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*chrome_sd_9224*' }} | "
        f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"],
        capture_output=True, creationflags=NW)
    time.sleep(4)

def launch_chrome():
    subprocess.Popen([CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
                      "https://www.ams.org/"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NW)
    for _ in range(20):
        time.sleep(2)
        if chrome_alive():
            print(f"  Chrome {PORT} up", flush=True); return True
    return False

def run_batch():
    cmd = [sys.executable, "-u", str(PROJECT / "jams_download.py"),
           "--cdp", f"http://127.0.0.1:{PORT}", "--delay", str(DELAY), "--budget", str(BUDGET)]
    r = subprocess.run(cmd, cwd=str(PROJECT), timeout=BUDGET + 180, capture_output=True, creationflags=NW)
    return r.returncode

batch = 0
stale = 0
while True:
    remaining = count_remaining()
    print(f"\n=== JAMS Batch {batch+1}: {remaining} remaining ({68-remaining}/68) ===", flush=True)
    if remaining == 0:
        print("ALL DONE — free JAMS 2018-2020 complete (68/68)!", flush=True); break
    if not chrome_alive():
        print("  Chrome down/hung, relaunching...", flush=True)
        kill_chrome()
        if not launch_chrome():
            print("  Chrome failed, retry 15s...", flush=True); time.sleep(15); continue
    before = 68 - remaining
    try:
        run_batch()
    except subprocess.TimeoutExpired:
        print("  Batch timed out; relaunching Chrome...", flush=True); kill_chrome(); continue
    except Exception as e:
        print(f"  Batch error: {e}; 10s...", flush=True); time.sleep(10)
    after = 68 - count_remaining()
    if after <= before:
        stale += 1
        # nudge Chrome if a couple of passes made no progress (CDP may have wedged)
        if stale >= 2:
            print("  no progress 2 passes — relaunching Chrome...", flush=True)
            kill_chrome(); stale = 0
    else:
        stale = 0
    time.sleep(5)
    batch += 1
