"""
gscs20 (Journal of Statistical Computation and Simulation) — full download for
2019-2026 via IIIT-Delhi institutional access (T&F is in ONOS).

Stable architecture: ONE Playwright-managed persistent Chrome (no CDP reconnect,
which was crashing). Cookies persist to the profile across runs.

Modes:
  login   : open browser, wait for you to log in via IIIT-Delhi, verify access.
  run     : download all remaining 2019-2026 gscs20 PDFs (resume-safe, time-boxed).
  merge   : merge downloaded PDFs per issue.
Profile persists login between runs (clean exit flushes cookies).
"""
import json, sys, time, base64, re, argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
PROFILE = "C:/Users/acer/AppData/Local/Temp/chrome_iiitd_profile"
DOIS = json.loads((PROJECT/"gscs20_dois.json").read_text(encoding='utf-8'))
DL = PROJECT/"gscs20_downloads"; DL.mkdir(exist_ok=True)
PROG = PROJECT/"gscs20_iiitd_progress.json"
MERGED = PROJECT/"gscs20_merged"; MERGED.mkdir(exist_ok=True)
CLEAN = PROJECT/"gscs20_clean"; CLEAN.mkdir(exist_ok=True)
CLEANER = PROJECT/"pdf_clean.py"
ISSN="00949655"
CHROME="C:/Program Files/Google/Chrome/Application/chrome.exe"
TEST_DOI="10.1080/00949655.2019.1602125"  # paywalled 2019 article

def safe(s): return re.sub(r'[^A-Za-z0-9._-]','_',s)
def load_prog():
    if PROG.exists(): return json.loads(PROG.read_text(encoding='utf-8'))
    return {"done":{}, "failed":[]}
def save_prog(p): PROG.write_text(json.dumps(p,indent=2,ensure_ascii=False),encoding='utf-8')

def wait_cf(pg,m=20):
    for _ in range(m):
        try:
            if 'just a moment' not in pg.title().lower(): return True
        except: pass
        time.sleep(1)
    return False

def fetch_pdf(pg, doi):
    r=pg.evaluate("""async(url)=>{try{
        const r=await fetch(url,{credentials:'include',redirect:'follow'});
        const ct=r.headers.get('content-type')||'';
        const b=new Uint8Array(await r.arrayBuffer());let s='';
        for(let i=0;i<b.length;i+=32768)s+=String.fromCharCode.apply(null,b.subarray(i,Math.min(i+32768,b.length)));
        return{ct:ct,len:b.length,b64:btoa(s)};}catch(e){return{len:0}}}""",
        f"https://www.tandfonline.com/doi/pdf/{doi}?download=true")
    if r.get("len",0)>10000:
        d=base64.b64decode(r["b64"])
        if d[:5]==b"%PDF-": return d
    return None

def has_access(pg):
    pg.goto(f"https://doi.org/{TEST_DOI}", wait_until="domcontentloaded", timeout=40000)
    wait_cf(pg); time.sleep(1)
    return fetch_pdf(pg, TEST_DOI) is not None

CDP_URL="http://127.0.0.1:9222"
class CtxWrap:
    """Wrap CDP browser so .close()/.pages behave like a context, without killing Chrome."""
    def __init__(self, br):
        self.br=br; self.ctx=br.contexts[0]
    @property
    def pages(self): return self.ctx.pages
    def new_page(self): return self.ctx.new_page()
    def close(self):
        # don't close Chrome (keep session alive); just detach
        try: self.br.close()
        except: pass

def open_ctx(p, headed=True):
    br=p.chromium.connect_over_cdp(CDP_URL)
    return CtxWrap(br)

def cmd_login():
    with sync_playwright() as p:
        ctx=open_ctx(p, headed=True)
        pg=ctx.pages[0] if ctx.pages else ctx.new_page()
        print("Checking existing access...")
        if has_access(pg):
            print(">>> Already logged in — T&F access WORKS."); ctx.close(); return
        print("Opening T&F institution login. Log in with IIIT-Delhi...")
        pg.goto("https://www.tandfonline.com/action/ssostart?redirectUri=%2Fjournals%2Fgscs20",
                wait_until="domcontentloaded", timeout=45000)
        wait_cf(pg)
        print("\n  In the browser: search 'Indraprastha' -> select IIIT-Delhi -> log in.")
        print("  Waiting up to 6 min for access to go live...\n")
        for i in range(72):
            time.sleep(5)
            try:
                if has_access(pg):
                    print(f">>> ACCESS CONFIRMED after ~{i*5}s! Login persisted."); ctx.close(); return
            except Exception: pass
            if i%6==0: print(f"  ...still waiting ({i*5}s)")
        print("Timed out. Re-run 'login' after completing sign-in."); ctx.close()

def build_work(prog):
    done=prog["done"]; work=[]
    for key,arts in DOIS["issues"].items():
        for a in arts:
            doi=a["doi"]
            m=re.search(r'\.(20[0-9]{2})\.',doi); yr=int(m.group(1)) if m else 0
            if yr<2019 or yr>2026: continue   # 2018 already done/in Drive
            if doi in done: continue
            work.append((key,a))
    return work

def cmd_run(max_seconds):
    prog=load_prog()
    with sync_playwright() as p:
        ctx=open_ctx(p, headed=True)
        pg=ctx.pages[0] if ctx.pages else ctx.new_page()
        if not has_access(pg):
            print("NO ACCESS — run 'login' first (and complete IIIT-Delhi sign-in)."); ctx.close(); return
        print(">>> Access OK. Warming up...");
        pg.goto("https://www.tandfonline.com/journals/gscs20",wait_until="domcontentloaded",timeout=40000); wait_cf(pg)
        work=build_work(prog)
        total_target=sum(1 for k,a in [(k,a) for k,arts in DOIS["issues"].items() for a in arts]
                         if 2019<=(int(re.search(r'\.(20[0-9]{2})\.',a['doi']).group(1)) if re.search(r'\.(20[0-9]{2})\.',a['doi']) else 0)<=2026)
        print(f"gscs20 2019-2026: target={total_target}, done={len(prog['done'])}, queue={len(work)}")
        t0=time.time(); ok=fail=0
        for key,a in work:
            if time.time()-t0>max_seconds:
                print(f"[time] {max_seconds}s budget reached, stopping cleanly"); break
            doi=a["doi"]; vol=a.get("volume","?"); iss=a.get("issue","?")
            bdir=DL/f"V{vol}I{iss}"; bdir.mkdir(exist_ok=True)
            fp=bdir/f"{safe(doi)}.pdf"
            if fp.exists() and fp.stat().st_size>10000 and fp.read_bytes()[:5]==b"%PDF-":
                prog["done"][doi]={"vol":vol,"iss":iss,"size":fp.stat().st_size}; ok+=1; continue
            try:
                pg.goto(f"https://doi.org/{doi}",wait_until="domcontentloaded",timeout=40000)
                wait_cf(pg); time.sleep(0.4)
                d=fetch_pdf(pg,doi)
                if d:
                    fp.write_bytes(d); prog["done"][doi]={"vol":vol,"iss":iss,"size":len(d)}; ok+=1
                    tag=f"OK {len(d)//1024}KB"
                else:
                    prog["failed"].append(doi); fail+=1; tag="FAIL"
            except Exception as e:
                prog["failed"].append(doi); fail+=1; tag=f"ERR {str(e)[:25]}"
            n=ok+fail
            if n%5==0 or not tag.startswith("OK"):
                print(f"  [{len(prog['done'])}/{total_target}] V{vol}I{iss} {doi[-11:]} {tag} {n/(time.time()-t0+0.1):.2f}/s",flush=True)
            if n%15==0: save_prog(prog)
        save_prog(prog); ctx.close()
        print(f"\nBatch: +{ok} ok, {fail} fail | total done={len(prog['done'])}/{total_target}")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("mode",choices=["login","run"])
    ap.add_argument("--max-seconds",type=int,default=500)
    a=ap.parse_args()
    if a.mode=="login": cmd_login()
    else: cmd_run(a.max_seconds)
