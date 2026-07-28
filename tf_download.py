"""Taylor & Francis downloader (multi-journal), 2018-2026, via JMI/ONOS IP + CDP Chrome.
Cloudflare-gated: warm up, wait for challenge to clear, then same-origin authenticated
fetch of /doi/pdf/<DOI>?download=true. conc=4 sweet spot (8+ throttles). Resume-safe.
Files: tandf_downloads/<code>/V<vol>I<iss>/<doi>.pdf
"""
import json, re, time, base64, asyncio, argparse
from pathlib import Path
from playwright.async_api import async_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DATA = json.loads((PROJECT/"tandf_dois.json").read_text(encoding="utf-8"))
DL = PROJECT/"tandf_downloads"; DL.mkdir(exist_ok=True)
CDP = "http://127.0.0.1:9222"
CONC = 4
DELAY = 0.6
MIN_YEAR = 2018
MAX_YEAR = 2026
ONLY = None   # comma-list of journal codes, or None = all in manifest

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def _int(x):
    try: return int(x)
    except Exception: return 10**9
def fpath(code, doi, vol, iss):
    return DL/code/f"V{safe(vol)}I{safe(iss)}"/f"{safe(doi)}.pdf"
def valid_pdf(fp):
    try: return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception: return False

def build_work():
    work = []
    codes = ONLY.split(",") if ONLY else list(DATA["journals"].keys())
    for code in codes:
        j = DATA["journals"][code]
        for k, arts in j["issues"].items():
            for a in arts:
                y = a.get("year", 0)
                if not (MIN_YEAR <= y <= MAX_YEAR): continue
                vol = a.get("volume", "?"); iss = a.get("issue", "?")
                if valid_pdf(fpath(code, a["doi"], vol, iss)): continue
                work.append((code, a["doi"], vol, iss, y))
    work.sort(key=lambda w: (w[0], w[4], _int(w[2]), _int(w[3]), w[1]))
    return [(c, d, v, i) for (c, d, v, i, y) in work]

FETCH_JS = """async(u)=>{try{
    const ac=new AbortController(); const to=setTimeout(()=>ac.abort(),35000);
    const r=await fetch(u,{credentials:'include',redirect:'follow',signal:ac.signal,headers:{'Accept':'application/pdf,*/*'}});
    clearTimeout(to);
    const b=new Uint8Array(await r.arrayBuffer());let s='';const C=0x8000;
    for(let i=0;i<b.length;i+=C)s+=String.fromCharCode.apply(null,b.subarray(i,Math.min(i+C,b.length)));
    const t=String.fromCharCode.apply(null,b.subarray(0,4000)).toLowerCase();
    return {status:r.status,len:b.length,head:String.fromCharCode.apply(null,b.subarray(0,5)),
            notrec:t.includes('not been recognised')||t.includes('not recognised'),
            cf:t.includes('just a moment')||(r.url||'').includes('cdn-cgi'),b64:btoa(s)};
  }catch(e){return{status:0,len:0,head:'',notrec:false,cf:false,b64:''}}}"""

async def fetch_pdf(page, doi):
    try:
        r = await asyncio.wait_for(
            page.evaluate(FETCH_JS, f"https://www.tandfonline.com/doi/pdf/{doi}?download=true"),
            timeout=22)
    except Exception:
        return None, {"status": 0, "timeout": True}
    if r.get("head") == "%PDF-" and r.get("len", 0) > 10000:
        return base64.b64decode(r["b64"]), r
    return None, r

async def wait_cf(page, m=25):
    for _ in range(m):
        try:
            if "just a moment" not in (await page.title()).lower(): return True
        except Exception: pass
        await asyncio.sleep(1)
    return False

async def dl_one(page, code, doi, vol, iss):
    fp = fpath(code, doi, vol, iss); fp.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            data, r = await fetch_pdf(page, doi)
            if data:
                fp.write_bytes(data)
                if valid_pdf(fp): return True, len(data)
            if r.get("notrec"):
                return False, -1   # IP not recognised — signal caller
            # CF or transient: re-warm + backoff
            await asyncio.sleep(3 * (attempt + 1))
            try:
                await page.goto("https://www.tandfonline.com/", wait_until="domcontentloaded", timeout=40000)
                await wait_cf(page)
            except Exception: pass
        except Exception:
            await asyncio.sleep(3 * (attempt + 1))
    return False, 0

async def worker(ctx, queue, stats, t0, budget):
    page = await ctx.new_page()
    try:
        await page.goto("https://www.tandfonline.com/", wait_until="domcontentloaded", timeout=45000)
        await wait_cf(page)
    except Exception: pass
    try:
        while True:
            if time.time()-t0 > budget: return
            try: code, doi, vol, iss = queue.get_nowait()
            except asyncio.QueueEmpty: return
            ok, sz = await dl_one(page, code, doi, vol, iss)
            if ok: stats["ok"] += 1; tag = f"OK {sz//1024}KB"
            elif sz == -1: stats["notrec"] += 1; tag = "IP-NOT-RECOGNISED"
            else: stats["fail"] += 1; tag = "FAIL"
            n = stats["ok"]+stats["fail"]+stats["notrec"]
            if n % 10 == 0 or not ok:
                el = time.time()-t0
                print(f"  [{stats['base']+stats['ok']}/{stats['total']}] {code} V{vol}I{iss} {doi[-12:]} {tag} {stats['ok']/el:.2f}/s ({el:.0f}s)", flush=True)
            await asyncio.sleep(DELAY)
    finally:
        await page.close()

async def main(budget):
    work = build_work()
    codes = ONLY.split(",") if ONLY else list(DATA["journals"].keys())
    base = sum(1 for code in codes for k,arts in DATA["journals"][code]["issues"].items() for a in arts
               if MIN_YEAR<=a.get("year",0)<=MAX_YEAR and valid_pdf(fpath(code,a["doi"],a.get("volume","?"),a.get("issue","?"))))
    total = base + len(work)
    print(f"T&F {codes} {MIN_YEAR}-{MAX_YEAR}: target={total}, on_disk={base}, queue={len(work)}, conc={CONC}, budget={budget}s")
    if not work:
        print("Queue empty — all done."); return
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        warm = await ctx.new_page()
        await warm.goto("https://www.tandfonline.com/", wait_until="domcontentloaded", timeout=45000)
        await wait_cf(warm)
        print("warmup:", (await warm.title())[:50])
        # pre-flight: probe a few queued articles; only a genuine IP-not-recognised aborts.
        probe = None; r = {}
        for pi in range(min(4, len(work))):
            probe, r = await fetch_pdf(warm, work[pi][1])
            if probe or r.get("notrec"):
                break
            await asyncio.sleep(2)
        egress = await warm.evaluate("async()=>{try{return await (await fetch('https://api.ipify.org')).text()}catch(e){return '?'}}")
        await warm.close()
        if r.get("notrec"):
            print("="*60)
            print(f"IP {egress} NOT RECOGNISED by T&F. Reconnect to a recognised JMI IP. Aborting.")
            print("="*60); return
        if not probe:
            print(f"pre-flight non-PDF (notrec=False) on IP {egress} — likely transient throttle; proceeding, workers retry.")
        else:
            print(f"access OK (egress {egress}) — starting.")
        queue = asyncio.Queue()
        for it in work: queue.put_nowait(it)
        stats = {"ok":0,"fail":0,"notrec":0,"base":base,"total":total}
        t0 = time.time()
        await asyncio.gather(*[worker(ctx, queue, stats, t0, budget) for _ in range(CONC)])
        el = time.time()-t0
        print(f"\nBatch {el:.0f}s: +{stats['ok']} ok, {stats['fail']} fail, {stats['notrec']} not-recognised | on_disk ~{base+stats['ok']}/{total} | remaining ~{len(work)-stats['ok']}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=540)
    ap.add_argument("--conc", type=int, default=CONC)
    ap.add_argument("--delay", type=float, default=DELAY)
    ap.add_argument("--only", type=str, default=None, help="comma journal codes e.g. lagb20")
    ap.add_argument("--min-year", type=int, default=MIN_YEAR)
    ap.add_argument("--max-year", type=int, default=MAX_YEAR)
    a = ap.parse_args()
    CONC=a.conc; DELAY=a.delay; ONLY=a.only; MIN_YEAR=a.min_year; MAX_YEAR=a.max_year
    asyncio.run(main(a.budget))
