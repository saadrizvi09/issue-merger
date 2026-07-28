"""Nature Physics (nphys, ISSN 1745-2473) downloader, 2018-2026, via JMI/ONOS IP.
Pure Python concurrent curl (NO browser): GET nature.com/articles/<id>.pdf where
<id> = DOI without the '10.1038/' prefix. Resume-safe by disk, time-boxed.
FULLY SEPARATE from any IOP/JCAP browser work (no CDP, no port, own folder/files).
Files: nphys_downloads/V<vol>I<iss>/<doi>.pdf
"""
import json, re, time, argparse, subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DATA = json.loads((PROJECT/"nphys_dois.json").read_text(encoding="utf-8"))
DL = PROJECT/"nphys_downloads"; DL.mkdir(exist_ok=True)
CONC = 8
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def fpath(doi, vol, iss):
    return DL/f"V{safe(vol)}I{safe(iss)}"/f"{safe(doi)}.pdf"
def valid_pdf(fp):
    try: return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception: return False

def build_work():
    work = []
    for k, arts in DATA["issues"].items():
        for a in arts:
            if not (2018 <= a.get("year", 0) <= 2026): continue
            if valid_pdf(fpath(a["doi"], a["volume"], a["issue"])): continue
            work.append((a["doi"], a["volume"], a["issue"]))
    return work

def art_id(doi):
    # 10.1038/s41567-023-01967-y -> s41567-023-01967-y
    return doi.split("/", 1)[1] if "/" in doi else doi

def download(item):
    doi, vol, iss = item
    fp = fpath(doi, vol, iss); fp.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://www.nature.com/articles/{art_id(doi)}.pdf"
    for attempt in range(4):
        try:
            subprocess.run(["curl", "-sL", "--max-time", "90", "-A", UA, "-o", str(fp), url],
                           capture_output=True, timeout=120)
            if valid_pdf(fp):
                return (item, True, fp.stat().st_size)
        except Exception:
            pass
        time.sleep(1.0*(attempt+1))
    return (item, False, 0)

def on_disk():
    return sum(1 for k,arts in DATA["issues"].items() for a in arts
               if 2018<=a.get("year",0)<=2026 and valid_pdf(fpath(a["doi"],a["volume"],a["issue"])))

def main(budget):
    work = build_work()
    total = DATA["total_articles"]; have0 = on_disk()
    print(f"nphys 2018-2026: target={total}, on_disk={have0}, queue={len(work)}, conc={CONC}, budget={budget}s", flush=True)
    if not work:
        print("Queue empty — all done."); return
    t0 = time.time(); ok = fail = 0
    ex = ThreadPoolExecutor(max_workers=CONC)
    futs = {ex.submit(download, it): it for it in work}
    try:
        for fut in as_completed(futs):
            item, good, sz = fut.result()
            if good: ok += 1
            else: fail += 1
            n = ok + fail
            if n % 25 == 0 or not good:
                el = time.time()-t0
                print(f"  [{have0+ok}/{total}] {item[0][-22:]} {'OK '+str(sz//1024)+'KB' if good else 'FAIL'} {n/el:.2f}/s ({el:.0f}s)", flush=True)
            if time.time()-t0 > budget:
                print("  [time budget reached, stopping]"); break
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    el = time.time()-t0
    print(f"\nBatch {el:.0f}s: +{ok} ok, {fail} fail | on_disk ~{have0+ok}/{total} | remaining ~{len(work)-ok}", flush=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=560)
    ap.add_argument("--conc", type=int, default=CONC)
    a = ap.parse_args()
    CONC = a.conc
    main(a.budget)
