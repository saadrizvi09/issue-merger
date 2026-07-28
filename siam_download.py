"""SIAM J. Discrete Mathematics (sjdmec, ISSN 0895-4801) downloader, 2018-2026, via
JMI/ONOS IP + CDP Chrome on port 9223 (SEPARATE from IOP's 9222).

Atypon/Cloudflare-gated (like T&F): warm up epubs.siam.org, wait for CF, then same-origin
authenticated fetch of /doi/pdf/<DOI>?download=true. Rate-limited. Resume-safe by disk.
Files: siam_downloads/V<vol>I<iss>/<doi>.pdf   Progress: siam_progress.json
"""
import json, re, time, base64, asyncio, argparse
from pathlib import Path
from playwright.async_api import async_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DOIS = json.loads((PROJECT/"siam_sjdmec_dois.json").read_text(encoding="utf-8"))
DL = PROJECT/"siam_downloads"; DL.mkdir(exist_ok=True)
CDP = "http://127.0.0.1:9223"   # <-- SIAM's own Chrome, NOT IOP's 9222
CONC = 2
DELAY = 2.0
MIN_YEAR = 2018
MAX_YEAR = 2026

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def _int(x):
    try: return int(x)
    except Exception: return 10**9
def fpath(doi, vol, iss):
    return DL/f"V{safe(vol)}I{safe(iss)}"/f"{safe(doi)}.pdf"
def valid_pdf(fp):
    try: return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception: return False

def build_work():
    work = []
    for k, arts in DOIS["issues"].items():
        for a in arts:
            y = a.get("year", 0)
            if not (MIN_YEAR <= y <= MAX_YEAR): continue
            vol = a.get("volume", "?"); iss = a.get("issue", "?")
            if valid_pdf(fpath(a["doi"], vol, iss)): continue
            work.append((a["doi"], vol, iss, y))
    work.sort(key=lambda w: (w[3], _int(w[1]), _int(w[2]), w[0]))
    return [(d, v, i) for (d, v, i, y) in work]

FETCH_JS = """async(u)=>{try{
    const r=await fetch(u,{credentials:'include',redirect:'follow',headers:{'Accept':'application/pdf,*/*'}});
    const b=new Uint8Array(await r.arrayBuffer());let s='';const C=0x8000;
    for(let i=0;i<b.length;i+=C)s+=String.fromCharCode.apply(null,b.subarray(i,Math.min(i+C,b.length)));
    const t=String.fromCharCode.apply(null,b.subarray(0,5000)).toLowerCase();
    return {status:r.status,len:b.length,head:String.fromCharCode.apply(null,b.subarray(0,5)),
            paywall:t.includes('get access')||t.includes('purchase')||t.includes('institutional')||t.includes('not been recognised')||t.includes('sign in'),
            cf:t.includes('just a moment'),b64:btoa(s)};
  }catch(e){return{status:0,len:0,head:'',paywall:false,cf:false,b64:''}}}"""

async def fetch_pdf(page, doi):
    r = await page.evaluate(FETCH_JS, f"https://epubs.siam.org/doi/pdf/{doi}?download=true")
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

async def dl_one(page, doi, vol, iss):
    fp = fpath(doi, vol, iss); fp.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            data, r = await fetch_pdf(page, doi)
            if data:
                fp.write_bytes(data)
                if valid_pdf(fp): return True, len(data)
            await asyncio.sleep(3 * (attempt + 1))
            try:
                await page.goto("https://epubs.siam.org/", wait_until="domcontentloaded", timeout=40000)
                await wait_cf(page)
            except Exception: pass
        except Exception:
            await asyncio.sleep(3 * (attempt + 1))
    return False, 0

async def worker(ctx, queue, stats, t0, budget):
    page = await ctx.new_page()
    try:
        await page.goto("https://epubs.siam.org/", wait_until="domcontentloaded", timeout=45000)
        await wait_cf(page)
    except Exception: pass
    try:
        while True:
            if time.time()-t0 > budget: return
            try: doi, vol, iss = queue.get_nowait()
            except asyncio.QueueEmpty: return
            ok, sz = await dl_one(page, doi, vol, iss)
            if ok: stats["ok"] += 1; tag = f"OK {sz//1024}KB"
            else: stats["fail"] += 1; tag = "FAIL"
            n = stats["ok"]+stats["fail"]
            if n % 10 == 0 or not ok:
                el = time.time()-t0
                print(f"  [{stats['base']+stats['ok']}/{stats['total']}] V{vol}I{iss} {doi[-12:]} {tag} {stats['ok']/el:.2f}/s ({el:.0f}s)", flush=True)
            await asyncio.sleep(DELAY)
    finally:
        await page.close()

async def main(budget):
    work = build_work()
    base = sum(1 for k,arts in DOIS["issues"].items() for a in arts
               if MIN_YEAR<=a.get("year",0)<=MAX_YEAR and valid_pdf(fpath(a["doi"],a.get("volume","?"),a.get("issue","?"))))
    total = base + len(work)
    print(f"SIAM sjdmec {MIN_YEAR}-{MAX_YEAR}: target={total}, on_disk={base}, queue={len(work)}, conc={CONC}, delay={DELAY}, budget={budget}s")
    from collections import Counter
    qc = Counter(a.get("year") for k,arts in DOIS["issues"].items() for a in arts
                 if not valid_pdf(fpath(a["doi"],a.get("volume","?"),a.get("issue","?"))) and MIN_YEAR<=a.get("year",0)<=MAX_YEAR)
    print("queue by year:", dict(sorted(qc.items())))
    if not work:
        print("Queue empty — all done."); return
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        warm = await ctx.new_page()
        await warm.goto("https://epubs.siam.org/", wait_until="domcontentloaded", timeout=45000)
        await wait_cf(warm)
        print("warmup:", (await warm.title())[:50])
        probe = None; r = {}
        for pi in range(min(4, len(work))):
            probe, r = await fetch_pdf(warm, work[pi][0])
            if probe: break
            await asyncio.sleep(2)
        egress = await warm.evaluate("async()=>{try{return await (await fetch('https://api.ipify.org')).text()}catch(e){return '?'}}")
        await warm.close()
        if not probe:
            print("="*60)
            print(f"NO SIAM ACCESS on IP {egress} (paywall={r.get('paywall')}, cf={r.get('cf')}, status={r.get('status')}).")
            print("SIAM may not be covered by ONOS, or IP not recognised. Aborting.")
            print("="*60); return
        print(f"access OK (egress {egress}) — starting.")
        queue = asyncio.Queue()
        for it in work: queue.put_nowait(it)
        stats = {"ok":0,"fail":0,"base":base,"total":total}
        t0 = time.time()
        await asyncio.gather(*[worker(ctx, queue, stats, t0, budget) for _ in range(CONC)])
        el = time.time()-t0
        print(f"\nBatch {el:.0f}s: +{stats['ok']} ok, {stats['fail']} fail | on_disk ~{base+stats['ok']}/{total} | remaining ~{len(work)-stats['ok']}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=540)
    ap.add_argument("--conc", type=int, default=CONC)
    ap.add_argument("--delay", type=float, default=DELAY)
    ap.add_argument("--min-year", type=int, default=MIN_YEAR)
    ap.add_argument("--max-year", type=int, default=MAX_YEAR)
    a = ap.parse_args()
    CONC=a.conc; DELAY=a.delay; MIN_YEAR=a.min_year; MAX_YEAR=a.max_year
    asyncio.run(main(a.budget))
