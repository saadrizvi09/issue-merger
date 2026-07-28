"""
SCIENCE 2018 COMPLETE DOWNLOADER — via authenticated Chrome (ONOS/JMI) over CDP.

- Loads science_2018_articles.json (all 4,687 DOIs, 51 issues + no-issue items)
- For each: visit article page (sets session) -> browser fetch /doi/pdf/<DOI>
- Saves real %PDF- only, resume-safe (progress json)
- Per-issue folders V<vol>I<issue>/ ; no-issue items in _noissue/
- Reports coverage honestly; never silently skips

Run FOREGROUND, resume-safe (re-run until complete). CDP Chrome on :9222.
"""
import json, sys, time, base64, re
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
ART_FILE = PROJECT / "science_2018_articles.json"
DL_DIR = PROJECT / "science_2018_downloads"
DL_DIR.mkdir(exist_ok=True)
PROG_FILE = PROJECT / "science_2018_progress.json"
CDP = "http://127.0.0.1:9222"

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', s)

def load_prog():
    if PROG_FILE.exists():
        return json.loads(PROG_FILE.read_text(encoding='utf-8'))
    return {"done": {}, "nopdf": [], "failed": []}

def save_prog(p): PROG_FILE.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding='utf-8')

def wait_cf(pg, m=25):
    for _ in range(m):
        t = pg.title().lower()
        if 'just a moment' not in t and 'loading http' not in t:
            return True
        time.sleep(1)
    return False

def fetch_pdf(pg, doi):
    r = pg.evaluate("""async(url)=>{try{
        const r=await fetch(url,{credentials:'include',redirect:'follow'});
        const ct=r.headers.get('content-type')||'';
        const b=new Uint8Array(await r.arrayBuffer());
        let s='';for(let i=0;i<b.length;i+=32768)s+=String.fromCharCode.apply(null,b.subarray(i,Math.min(i+32768,b.length)));
        return {status:r.status,ct:ct,len:b.length,b64:btoa(s)};
    }catch(e){return{status:0,err:e.message};}}""", f"https://www.science.org/doi/pdf/{doi}")
    if r.get("len", 0) > 8000:
        data = base64.b64decode(r["b64"])
        if data[:5] == b"%PDF-":
            return data
    return None

def check_access(pg):
    """Return True if authenticated (a known paywalled research article yields a PDF)."""
    doi = "10.1126/science.aar4301"
    try:
        pg.goto(f"https://www.science.org/doi/{doi}", wait_until="domcontentloaded", timeout=40000)
    except: pass
    wait_cf(pg); time.sleep(1)
    return fetch_pdf(pg, doi) is not None

def build_worklist(data, prog):
    """Return list of (bucket, doi, meta). bucket = V<vol>I<iss> or '_noissue'."""
    done = prog.get("done", {})
    nopdf = set(prog.get("nopdf", []))
    work = []
    for key in sorted(data["issues"].keys(), key=lambda x:(int(x[1:].split('I')[0]), int(x.split('I')[1]))):
        for a in data["issues"][key]:
            doi = a["doi"]
            if doi in done or doi in nopdf: continue
            work.append((key, doi, a))
    for a in data.get("no_issue", []):
        doi = a["doi"]
        if doi in done or doi in nopdf: continue
        work.append(("_noissue", doi, a))
    return work

def main():
    data = json.loads(ART_FILE.read_text(encoding='utf-8'))
    prog = load_prog()

    total = data["total_articles"]
    done = prog.get("done", {})
    print(f"Science 2018 | total={total} | done={len(done)} | nopdf={len(prog.get('nopdf',[]))} | failed={len(prog.get('failed',[]))}")

    work = build_worklist(data, prog)
    print(f"To attempt: {len(work)}")
    if not work:
        print("Nothing to do."); report(data, prog); return

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        pg = ctx.new_page()
        pg.goto("https://www.science.org/", wait_until="domcontentloaded", timeout=45000)
        wait_cf(pg)

        print("Checking authenticated access...")
        if not check_access(pg):
            print("\n*** NOT AUTHENTICATED — research PDFs are paywalled from this session. ***")
            print("Log in via the Chrome window (SeamlessAccess -> your institution), then re-run.")
            pg.close(); return
        print(">>> Authenticated! Full PDF access confirmed. Starting download.\n")

        ok = fail = nopdf = 0
        t0 = time.time()
        for i, (bucket, doi, meta) in enumerate(work, 1):
            bdir = DL_DIR / bucket
            bdir.mkdir(exist_ok=True)
            fp = bdir / f"{safe(doi)}.pdf"
            if fp.exists() and fp.stat().st_size > 8000 and fp.read_bytes()[:5] == b"%PDF-":
                done[doi] = {"bucket": bucket, "size": fp.stat().st_size}; ok += 1; continue

            try:
                pg.goto(f"https://www.science.org/doi/{doi}", wait_until="domcontentloaded", timeout=40000)
                wait_cf(pg); time.sleep(0.4)
                data_pdf = fetch_pdf(pg, doi)
                if data_pdf:
                    fp.write_bytes(data_pdf)
                    done[doi] = {"bucket": bucket, "size": len(data_pdf)}
                    ok += 1
                    tag = f"OK {len(data_pdf)//1024}KB"
                else:
                    # No PDF (likely online-only news/correction) — record, don't retry forever
                    prog.setdefault("nopdf", []).append(doi); nopdf += 1
                    tag = "noPDF"
            except Exception as e:
                prog.setdefault("failed", []).append(doi); fail += 1
                tag = f"ERR {str(e)[:24]}"

            rate = i / (time.time()-t0)
            if i % 5 == 0 or tag.startswith("OK") is False:
                eta = (len(work)-i)/rate/60 if rate>0 else 0
                print(f"[{i}/{len(work)}] {bucket:11s} {doi:34s} {tag:12s} {rate:.2f}/s ETA {eta:.0f}m", flush=True)

            if i % 25 == 0:
                prog["done"] = done; save_prog(prog)

        prog["done"] = done; save_prog(prog)
        pg.close()
        print(f"\nRun done: ok={ok} nopdf={nopdf} fail={fail}")

    report(data, prog)

def report(data, prog):
    done = prog.get("done", {})
    from collections import defaultdict
    byk_have = defaultdict(int); byk_tot = defaultdict(int)
    for key, arts in data["issues"].items():
        for a in arts:
            byk_tot[key]+=1
            if a["doi"] in done: byk_have[key]+=1
    print(f"\n{'='*56}\nSCIENCE 2018 COVERAGE\n{'='*56}")
    full=part=0
    for key in sorted(byk_tot, key=lambda x:(int(x[1:].split('I')[0]),int(x.split('I')[1]))):
        h,t=byk_have[key],byk_tot[key]
        mark = "OK" if h==t else "  "
        if h==t: full+=1
        else: part+=1
        print(f"  {mark} {key}: {h}/{t}")
    print(f"\nIssues complete: {full}/{len(byk_tot)} | partial: {part}")
    print(f"Total downloaded: {len(done)} | no-PDF items: {len(prog.get('nopdf',[]))} | failed: {len(prog.get('failed',[]))}")

if __name__ == "__main__":
    main()
