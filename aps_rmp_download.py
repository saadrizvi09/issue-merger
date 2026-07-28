"""APS Reviews of Modern Physics (rmp, ISSN 0034-6861) downloader, 2018-2026, via
JMI/ONOS IP + CDP Chrome on port 9223 (SEPARATE from JCAP's 9222 and nphys curl).

Cloudflare-gated (browser passes): warm up journals.aps.org/rmp, then same-origin
authenticated fetch of /rmp/pdf/<DOI>. RMP PDFs are big (10-20 MB), so conc is low.
Resume-safe by disk. Files: aps_rmp_downloads/V<vol>I<iss>/<doi>.pdf
"""
import json, re, time, base64, asyncio, argparse
from pathlib import Path
from playwright.async_api import async_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DOIS = json.loads((PROJECT/"aps_rmp_dois.json").read_text(encoding="utf-8"))
DL = PROJECT/"aps_rmp_downloads"; DL.mkdir(exist_ok=True)
CDP = "http://127.0.0.1:9223"     # <-- APS's own Chrome, NOT JCAP's 9222
CONC = 2
DELAY = 1.5
MIN_YEAR = 2018
MAX_YEAR = 2026

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def _int(x):
    try: return int(x)
    except Exception: return 10**9
def url_doi(doi):
    # Crossref lowercases the DOI; APS PDF endpoint wants the canonical case.
    return doi.replace("revmodphys", "RevModPhys")
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

# Lightweight access probe: check status + content-type WITHOUT downloading the (huge) body.
PROBE_JS = """async(u)=>{try{
    const r=await fetch(u,{credentials:'include',redirect:'follow'});
    const ct=(r.headers.get('content-type')||'').toLowerCase();
    try{ if(r.body&&r.body.cancel) r.body.cancel(); }catch(e){}
    return {status:r.status, pdf:ct.includes('pdf'), ct:ct.slice(0,20)};
  }catch(e){return{status:0,pdf:false,ct:''}}}"""

# Blob-download: fetch into a Blob in-browser, then click an <a download> so Chrome saves it.
# Playwright's expect_download streams the file to disk in chunks -> no giant base64, no MemoryError.
BLOB_JS = """async(u)=>{
    const r=await fetch(u,{credentials:'include',redirect:'follow'});
    if(!r.ok) throw new Error('status '+r.status);
    const b=await r.blob();
    if(b.size<10000) throw new Error('too small '+b.size);
    const a=document.createElement('a');
    a.href=URL.createObjectURL(b); a.download='paper.pdf';
    document.body.appendChild(a); a.click();
    setTimeout(()=>{URL.revokeObjectURL(a.href); a.remove();}, 15000);
    return b.size;
}"""

async def probe_access(page, doi):
    try:
        r = await asyncio.wait_for(page.evaluate(PROBE_JS, f"https://journals.aps.org/rmp/pdf/{url_doi(doi)}"), timeout=40)
        return bool(r.get("pdf")), r
    except Exception:
        return False, {"status": 0, "timeout": True}

async def wait_cf(page, m=25):
    for _ in range(m):
        try:
            if "just a moment" not in (await page.title()).lower(): return True
        except Exception: pass
        await asyncio.sleep(1)
    return False

async def dl_one(page, doi, vol, iss):
    fp = fpath(doi, vol, iss); fp.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://journals.aps.org/rmp/pdf/{url_doi(doi)}"
    for attempt in range(3):
        try:
            async with page.expect_download(timeout=150000) as di:
                await asyncio.wait_for(page.evaluate(BLOB_JS, url), timeout=150)
            dl = await di.value
            await dl.save_as(str(fp))
            if valid_pdf(fp): return True, fp.stat().st_size
        except Exception:
            pass
        await asyncio.sleep(3 * (attempt + 1))
        try:
            await page.goto("https://journals.aps.org/rmp/", wait_until="domcontentloaded", timeout=40000)
            await wait_cf(page)
        except Exception: pass
    return False, 0

async def worker(ctx, queue, stats, t0, budget):
    page = await ctx.new_page()
    try:
        await page.goto("https://journals.aps.org/rmp/", wait_until="domcontentloaded", timeout=45000)
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
            if n % 5 == 0 or not ok:
                el = time.time()-t0
                print(f"  [{stats['base']+stats['ok']}/{stats['total']}] V{vol}I{iss} {doi[-14:]} {tag} {stats['ok']/el:.2f}/s ({el:.0f}s)", flush=True)
            await asyncio.sleep(DELAY)
    finally:
        await page.close()

async def main(budget):
    work = build_work()
    base = sum(1 for k,arts in DOIS["issues"].items() for a in arts
               if MIN_YEAR<=a.get("year",0)<=MAX_YEAR and valid_pdf(fpath(a["doi"],a.get("volume","?"),a.get("issue","?"))))
    total = base + len(work)
    print(f"APS rmp {MIN_YEAR}-{MAX_YEAR}: target={total}, on_disk={base}, queue={len(work)}, conc={CONC}, budget={budget}s")
    if not work:
        print("Queue empty — all done."); return
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        warm = await ctx.new_page()
        await warm.goto("https://journals.aps.org/rmp/", wait_until="domcontentloaded", timeout=45000)
        await wait_cf(warm)
        print("warmup:", (await warm.title())[:45])
        probe = False; r = {}
        for pi in range(min(3, len(work))):
            probe, r = await probe_access(warm, work[pi][0])
            if probe: break
            await asyncio.sleep(2)
        egress = await warm.evaluate("async()=>{try{return await (await fetch('https://api.ipify.org')).text()}catch(e){return '?'}}")
        await warm.close()
        if not probe:
            print("="*60); print(f"NO APS ACCESS on IP {egress} (pay={r.get('pay')}, cf={r.get('cf')}, status={r.get('status')}). Aborting."); print("="*60)
            return
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
