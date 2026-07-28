"""
SCIENCE 2018 — full download via Sci-Hub (browser over CDP), resume-safe.

- Loads science_2018_articles.json (4,687 DOIs, 51 issues + no-issue).
- Sci-Hub flow: goto sci-hub.ru/<doi> (redirects to a live mirror), solve ALTCHA
  captcha if shown, extract /storage/...pdf link, browser-fetch the bytes.
- Fallback to green-OA PDF (science_2018_oa.json) when Sci-Hub misses.
- Saves per-issue V<vol>I<iss>/<doi>.pdf ; resume-safe progress json.
- Runs for --max-seconds then exits cleanly (re-run to continue; foreground-safe).

CDP Chrome must be on :9222 with VPN active (Sci-Hub reachable).
"""
import json, sys, time, base64, re, argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
ART = json.loads((PROJECT/"science_2018_articles.json").read_text(encoding='utf-8'))
OA  = {}
oaf = PROJECT/"science_2018_oa.json"
if oaf.exists(): OA = json.loads(oaf.read_text(encoding='utf-8'))
DL_DIR = PROJECT/"science_2018_downloads"; DL_DIR.mkdir(exist_ok=True)
PROG = PROJECT/"science_2018_progress.json"
CDP = "http://127.0.0.1:9222"
MIRRORS = ["https://sci-hub.ru", "https://sci-hub.se", "https://sci-hub.st"]

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', s)
def load_prog():
    if PROG.exists(): return json.loads(PROG.read_text(encoding='utf-8'))
    return {"done":{}, "notfound":[], "failed":[]}
def save_prog(p): PROG.write_text(json.dumps(p,indent=2,ensure_ascii=False), encoding='utf-8')

def origin_of(url):
    m=re.match(r'(https?://[^/]+)', url); return m.group(1) if m else url

def fetch_bytes(pg, url):
    r=pg.evaluate("""async(url)=>{try{
        const r=await fetch(url,{credentials:'include'});
        const b=new Uint8Array(await r.arrayBuffer());let s='';
        for(let i=0;i<b.length;i+=32768)s+=String.fromCharCode.apply(null,b.subarray(i,Math.min(i+32768,b.length)));
        return{len:b.length,b64:btoa(s)};}catch(e){return{len:0};}}""", url)
    if r.get("len",0)>10000:
        d=base64.b64decode(r["b64"])
        if d[:5]==b"%PDF-": return d
    return None

def is_captcha(pg):
    t=pg.title().lower()
    if 'robot' in t or 'are you' in t or 'moment' in t or 'ddos' in t:
        return True
    try:
        if pg.locator('.answer').count()>0 and pg.locator('#pdf, embed, iframe').count()==0:
            return True
    except: pass
    return False

def solve_captcha(pg, tries=2):
    for _ in range(tries):
        if not is_captcha(pg): return True
        try: pg.locator('.answer').first.click(timeout=4000)
        except: pass
        for _ in range(15):
            time.sleep(1)
            if not is_captcha(pg): return True
    return not is_captcha(pg)

def extract_and_fetch(pg, html):
    links=re.findall(r'(?:src|href)="([^"]*(?:/storage/[^"]*\.pdf|\.pdf)[^"]*)"', html, re.I)
    links+=re.findall(r"location\.href=['\"]([^'\"]+\.pdf[^'\"]*)", html)
    if not links:
        links+=re.findall(r'<(?:embed|iframe)[^>]+src="([^"]+)"', html, re.I)
    org=origin_of(pg.url)
    for u in dict.fromkeys(links):
        full = u if u.startswith('http') else ('https:'+u if u.startswith('//') else org+u)
        d=fetch_bytes(pg, full)
        if d: return d
    return None

def scihub_pdf(pg, doi):
    """Return PDF bytes, 'notfound', or None. Tries mirrors; re-solves captcha and retries once."""
    for mirror in MIRRORS:
        for attempt in range(2):
            try:
                pg.goto(f"{mirror}/{doi}", wait_until="domcontentloaded", timeout=35000)
                time.sleep(1.2)
                if is_captcha(pg):
                    solve_captcha(pg)
                    time.sleep(0.5)
                html=pg.content()
                low=html.lower()
                if 'article not found' in low or 'статья не найдена' in low or 'unfortunately, sci-hub' in low:
                    return "notfound"
                d=extract_and_fetch(pg, html)
                if d: return d
                # no link: if captcha still up, retry this mirror once; else next mirror
                if is_captcha(pg) and attempt==0:
                    solve_captcha(pg); continue
                break
            except Exception:
                break
    return None

def oa_pdf(pg, doi):
    """Fallback: green-OA repository PDF from Unpaywall scan."""
    rec=OA.get(doi)
    if not rec or not rec.get("pdf"): return None
    try:
        d=fetch_bytes(pg, rec["pdf"])
        return d
    except: return None

def build_work(prog):
    done=prog["done"]; nf=set(prog["notfound"])
    work=[]
    for key in sorted(ART["issues"].keys(), key=lambda x:(int(x[1:].split('I')[0]),int(x.split('I')[1]))):
        for a in ART["issues"][key]:
            if a["doi"] in done: continue
            work.append((key,a))
    for a in ART["no_issue"]:
        if a["doi"] in done: continue
        work.append(("_noissue",a))
    return work

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--max-seconds", type=int, default=540)
    ap.add_argument("--retry-notfound", action="store_true")
    args=ap.parse_args()

    prog=load_prog()
    if args.retry_notfound: prog["notfound"]=[]
    prog["failed"]=[]  # failures are transient (captcha/rate-limit) -> always retry
    work=build_work(prog)
    total=ART["total_articles"]
    print(f"Science 2018 | total={total} done={len(prog['done'])} notfound={len(prog['notfound'])} | queue={len(work)}")
    if not work:
        report(prog); return

    t0=time.time(); ok=nf=fail=0
    with sync_playwright() as p:
        br=p.chromium.connect_over_cdp(CDP)
        ctx=br.contexts[0]
        pg=ctx.pages[0] if ctx.pages else ctx.new_page()
        # warm sci-hub once
        try:
            pg.goto("https://sci-hub.ru/", wait_until="domcontentloaded", timeout=30000); time.sleep(1); solve_captcha(pg)
        except: pass

        consec_fail=0
        for key,a in work:
            if time.time()-t0 > args.max_seconds:
                print(f"[time] hit {args.max_seconds}s budget, stopping cleanly"); break
            # circuit breaker: many consecutive fails likely = VPN dropped / mirror down
            if consec_fail>=12:
                import urllib.request
                reachable=False
                for mm in MIRRORS:
                    try:
                        urllib.request.urlopen(mm, timeout=8); reachable=True; break
                    except: pass
                if not reachable:
                    print(f"[circuit-breaker] {consec_fail} consecutive fails and Sci-Hub unreachable "
                          f"(VPN down?). Stopping cleanly — nothing lost, re-run to resume."); break
                consec_fail=0  # reachable, keep going
            doi=a["doi"]; vol=a.get("volume"); iss=a.get("issue")
            vdir = key if key!="_noissue" else "_noissue"
            bdir=DL_DIR/vdir; bdir.mkdir(exist_ok=True)
            fp=bdir/f"{safe(doi)}.pdf"
            if fp.exists() and fp.stat().st_size>10000 and fp.read_bytes()[:5]==b"%PDF-":
                prog["done"][doi]={"bucket":vdir,"size":fp.stat().st_size}; ok+=1; continue

            res=scihub_pdf(pg, doi)
            src="scihub"
            if res is None or res=="notfound":
                # green-OA fallback
                oa=oa_pdf(pg, doi)
                if oa: res=oa; src="green-oa"
            if isinstance(res,(bytes,bytearray)):
                fp.write_bytes(res)
                prog["done"][doi]={"bucket":vdir,"size":len(res),"src":src}
                ok+=1; tag=f"OK {len(res)//1024}KB [{src}]"; consec_fail=0
            elif res=="notfound":
                prog["notfound"].append(doi); nf+=1; tag="notfound"; consec_fail=0
            else:
                prog["failed"].append(doi); fail+=1; tag="FAIL"; consec_fail+=1
            n=ok+nf+fail
            if n%5==0 or tag.startswith("OK")==False:
                rate=n/(time.time()-t0+0.1)
                print(f"  [{len(prog['done'])}/{total}] {key:11s} {doi:32s} {tag:16s} {rate:.2f}/s", flush=True)
            if n%20==0: save_prog(prog)
        pg.close() if False else None
        save_prog(prog)
    dt=time.time()-t0
    print(f"\nBatch: +{ok} ok, {nf} notfound, {fail} fail in {dt:.0f}s | total done={len(prog['done'])}/{total}")
    report(prog)

def report(prog):
    done=prog["done"]
    from collections import defaultdict
    have=defaultdict(int); tot=defaultdict(int)
    for key,arts in ART["issues"].items():
        for a in arts:
            tot[key]+=1
            if a["doi"] in done: have[key]+=1
    full=sum(1 for k in tot if have[k]==tot[k])
    print(f"Issues complete: {full}/{len(tot)} | downloaded {len(done)}/{ART['total_articles']} | notfound {len(prog['notfound'])} | failed {len(set(prog['failed']))}")

if __name__=="__main__":
    main()
