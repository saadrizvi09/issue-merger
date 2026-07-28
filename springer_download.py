"""Springer downloader — 4 journals (180/245/10626/10851), 2018-2026, via JMI/ONOS IP.
Pure Python concurrent HTTP (no browser): GET link.springer.com/content/pdf/<DOI>.pdf.
Resume-safe by disk, time-boxed, re-run until done. Files: springer_downloads/<jid>/V<vol>I<iss>/<doi>.pdf
"""
import json, re, time, argparse, subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DATA = json.loads((PROJECT/"springer_dois.json").read_text(encoding="utf-8"))
DL = PROJECT/"springer_downloads"; DL.mkdir(exist_ok=True)
PROG = PROJECT/"springer_progress.json"
CONC = 16
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def fpath(jid, doi, vol, iss):
    return DL/jid/f"V{safe(vol)}I{safe(iss)}"/f"{safe(doi)}.pdf"
def valid_pdf(fp):
    try: return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception: return False

def build_work():
    work = []
    for jid, j in DATA["journals"].items():
        for k, arts in j["issues"].items():
            for a in arts:
                if valid_pdf(fpath(jid, a["doi"], a["volume"], a["issue"])):
                    continue
                work.append((jid, a["doi"], a["volume"], a["issue"]))
    return work

def download(item):
    jid, doi, vol, iss = item
    fp = fpath(jid, doi, vol, iss); fp.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://link.springer.com/content/pdf/{doi}.pdf"
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

def counts_on_disk():
    have = {jid: 0 for jid in DATA["journals"]}
    for jid, j in DATA["journals"].items():
        for k, arts in j["issues"].items():
            for a in arts:
                if valid_pdf(fpath(jid, a["doi"], a["volume"], a["issue"])): have[jid] += 1
    return have

def main(budget):
    work = build_work()
    total = sum(j["total"] for j in DATA["journals"].values())
    have0 = sum(counts_on_disk().values())
    print(f"Springer 4 journals 2018-2026: target={total}, on_disk={have0}, queue={len(work)}, conc={CONC}, budget={budget}s")
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
                print(f"  [{have0+ok}/{total}] {item[0]} {item[1][-18:]} {'OK '+str(sz//1024)+'KB' if good else 'FAIL'} {n/el:.2f}/s ({el:.0f}s)", flush=True)
            if time.time()-t0 > budget:
                print("  [time budget reached, stopping]"); break
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    el = time.time()-t0
    print(f"\nBatch {el:.0f}s: +{ok} ok, {fail} fail | on_disk ~{have0+ok}/{total} | remaining ~{len(work)-ok}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=560)
    ap.add_argument("--conc", type=int, default=CONC)
    a = ap.parse_args()
    CONC = a.conc
    main(a.budget)
