"""
IOP J.Phys: Condensed Matter (ISSN 0953-8984) downloader, 2018-2026, via JMI/ONOS IP
through the running CDP Chrome.

PDF mechanism (simpler than T&F — no Cloudflare, no expect_download): authenticated
browser fetch of https://iopscience.iop.org/article/<DOI>/pdf -> base64 -> verify %PDF-.

Issue year = Crossref published-print year (already baked into the manifest's "year"),
NOT online-first. Resume-safe by disk. Rate-limited, time-boxed, re-run until done.
"""
import json, re, time, base64, asyncio, argparse
from pathlib import Path
from playwright.async_api import async_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
MANIFEST = PROJECT/"iop_0953-8984_dois.json"   # overridable via --manifest
DL = PROJECT/"iop_downloads"                    # overridable via --dldir
DOIS = None                                     # loaded in main() from MANIFEST
PROG = PROJECT/"iop_progress.json"
CDP = "http://127.0.0.1:9222"
CONC = 1        # IOP Radware bot-manager trips on concurrent fetch bursts; stay serial
DELAY = 2.0     # paced fetch avoids the bot manager (proven safe)
MIN_YEAR = 2018
MAX_YEAR = 2026
PREFLIGHT_DOI = "10.1088/1361-648x/ab7f6e"  # known-accessible article

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def _int(x):
    try: return int(x)
    except Exception: return 10**9

def fpath(doi, vol, iss):
    return DL/f"V{safe(vol)}I{safe(iss)}"/f"{safe(doi)}.pdf"

def valid_pdf(fp):
    try:
        return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception:
        return False

def load_prog():
    p = json.loads(PROG.read_text(encoding="utf-8")) if PROG.exists() else {}
    p.setdefault("downloaded", {}); p.setdefault("failed", [])
    return p
def save_prog(p): PROG.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")

def build_work():
    work = []
    for k, arts in DOIS["issues"].items():
        for a in arts:
            y = a.get("year", 0)
            if not (MIN_YEAR <= y <= MAX_YEAR):
                continue
            vol = a.get("volume", "?"); iss = a.get("issue", "?")
            if valid_pdf(fpath(a["doi"], vol, iss)):
                continue
            work.append((a["doi"], vol, iss, y))
    work.sort(key=lambda w: (w[3], _int(w[1]), _int(w[2]), w[0]))
    return [(d, v, i) for (d, v, i, y) in work]

FETCH_JS = """async(u)=>{try{
    const r=await fetch(u,{credentials:'include',redirect:'follow',headers:{'Accept':'application/pdf,*/*'}});
    const b=new Uint8Array(await r.arrayBuffer());let s='';const C=0x8000;
    for(let i=0;i<b.length;i+=C)s+=String.fromCharCode.apply(null,b.subarray(i,Math.min(i+C,b.length)));
    const bot=(r.url||'').includes('perfdrive') || (String.fromCharCode.apply(null,b.subarray(0,300)).toLowerCase().includes('bot manager'));
    return {status:r.status,len:b.length,head:String.fromCharCode.apply(null,b.subarray(0,5)),bot:bot,b64:btoa(s)};
  }catch(e){return{status:0,len:0,head:'',bot:false,b64:''}}}"""

async def fetch_pdf(page, doi):
    """Returns (bytes|None, bot_blocked)."""
    r = await page.evaluate(FETCH_JS, f"https://iopscience.iop.org/article/{doi}/pdf")
    if r.get("head") == "%PDF-" and r.get("len", 0) > 10000:
        return base64.b64decode(r["b64"]), False
    return None, bool(r.get("bot"))

JOURNAL_URL = "https://iopscience.iop.org/journal/0953-8984"

async def dl_one(page, doi, vol, iss):
    fp = fpath(doi, vol, iss); fp.parent.mkdir(exist_ok=True)
    for attempt in range(5):
        try:
            data, bot = await fetch_pdf(page, doi)
            if data:
                fp.write_bytes(data)
                if valid_pdf(fp):
                    return True, len(data)
            # non-PDF: transient soft-block or hard bot-block. Cool down + re-navigate
            # (real nav resets the rate window; fetch is same-origin again after).
            cd = 45 if bot else 6 * (attempt + 1)
            if bot:
                print(f"    [bot-block] cooling {cd}s ({doi[-8:]})", flush=True)
            await asyncio.sleep(cd)
            try:
                await page.goto(JOURNAL_URL, wait_until="domcontentloaded", timeout=40000)
                await asyncio.sleep(1)
            except Exception:
                pass
        except Exception:
            await asyncio.sleep(4 * (attempt + 1))
    return False, 0

async def worker(ctx, queue, prog, stats, t0, budget):
    page = await ctx.new_page()
    # must be ON an iopscience origin so credentialed fetch() is same-origin
    try:
        await page.goto("https://iopscience.iop.org/journal/0953-8984", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1)
    except Exception:
        pass
    try:
        while True:
            if time.time()-t0 > budget: return
            try: doi, vol, iss = queue.get_nowait()
            except asyncio.QueueEmpty: return
            ok, sz = await dl_one(page, doi, vol, iss)
            if ok:
                prog["downloaded"][doi] = {"vol": vol, "iss": iss, "size": sz}; stats["ok"] += 1
                tag = f"OK {sz//1024}KB"
            else:
                if doi not in prog["failed"]: prog["failed"].append(doi)
                stats["fail"] += 1; tag = "FAIL"
            n = stats["ok"]+stats["fail"]
            if n % 10 == 0 or not ok:
                el = time.time()-t0
                print(f"  [{stats['base']+stats['ok']}/{stats['total']}] V{vol}I{iss} {doi[-14:]} {tag} {n/el:.2f}/s ({el:.0f}s)", flush=True)
            if n % 20 == 0: save_prog(prog)
            await asyncio.sleep(DELAY)
    finally:
        await page.close()

async def main(budget):
    global DOIS
    DOIS = json.loads(MANIFEST.read_text(encoding="utf-8"))
    DL.mkdir(exist_ok=True)
    prog = load_prog()
    work = build_work()
    base = sum(1 for k,arts in DOIS["issues"].items() for a in arts
               if MIN_YEAR<=a.get("year",0)<=MAX_YEAR and valid_pdf(fpath(a["doi"],a.get("volume","?"),a.get("issue","?"))))
    total = base + len(work)
    print(f"IOP 0953-8984 2018-{MAX_YEAR}: target={total}, on_disk={base}, queue={len(work)}, conc={CONC}, delay={DELAY}, budget={budget}s")
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
        await warm.goto("https://iopscience.iop.org/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1)
        print("warmup:", (await warm.title())[:50])
        probe, _bot = await fetch_pdf(warm, PREFLIGHT_DOI)
        egress = await warm.evaluate("async()=>{try{return await (await fetch('https://api.ipify.org')).text()}catch(e){return '?'}}")
        await warm.close()
        if not probe:
            print("="*60); print(f"NO ACCESS on this IP (egress {egress}). Reconnect to JMI/ONOS. Aborting fast."); print("="*60)
            return
        print(f"access OK (egress {egress}) — starting.")
        queue = asyncio.Queue()
        for it in work: queue.put_nowait(it)
        stats = {"ok":0,"fail":0,"base":base,"total":total}
        t0 = time.time()
        await asyncio.gather(*[worker(ctx, queue, prog, stats, t0, budget) for _ in range(CONC)])
        save_prog(prog)
        el = time.time()-t0
        print(f"\nBatch {el:.0f}s: +{stats['ok']} ok, {stats['fail']} fail | on_disk ~{base+stats['ok']}/{total} | remaining ~{len(work)-stats['ok']}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=540)
    ap.add_argument("--delay", type=float, default=DELAY)
    ap.add_argument("--conc", type=int, default=CONC)
    ap.add_argument("--max-year", type=int, default=MAX_YEAR)
    ap.add_argument("--min-year", type=int, default=MIN_YEAR)
    ap.add_argument("--manifest", type=str, default=str(MANIFEST))
    ap.add_argument("--dldir", type=str, default=str(DL))
    a = ap.parse_args()
    DELAY = a.delay; CONC = a.conc; MAX_YEAR = a.max_year; MIN_YEAR = a.min_year
    MANIFEST = Path(a.manifest); DL = Path(a.dldir)
    asyncio.run(main(a.budget))
