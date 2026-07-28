"""
gscs20 (JSCS) — fast concurrent downloader for 2019-2026 via the running CDP Chrome
(already authenticated to T&F institutionally: IIIT-Delhi / ONOS).

Proven method: async multi-tab in ONE CDP context (shares Cloudflare clearance + IP),
expect_download around goto("/doi/pdf/<DOI>?download=true"). conc=4 sweet spot.

Resume-safe: skips any DOI whose PDF already exists on disk as a real %PDF-.
Time-boxed so it exits cleanly (Chrome dies if backgrounded on this machine) — re-run
until queue == 0.
"""
import json, re, time, asyncio, argparse
from pathlib import Path
from playwright.async_api import async_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DOIS = json.loads((PROJECT/"gscs20_dois.json").read_text(encoding="utf-8"))
DL = PROJECT/"gscs20_downloads"; DL.mkdir(exist_ok=True)
PROG = PROJECT/"gscs20_progress.json"
CDP = "http://127.0.0.1:9222"
CONC = 1
DELAY = 1.5   # seconds to pause between downloads (rate limiting; overridable via --delay)
MAX_YEAR = 2026   # only download years <= this (overridable via --max-year)

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', s)
def yr(doi):
    m = re.search(r'\.(20\d{2})\.', doi); return int(m.group(1)) if m else 0

def art_year(vol, doi):
    """Issue year = volume + 1930 (JSCS: vol88=2018 .. vol96=2026). Falls back to the
    DOI/online-first year only when volume is unknown ('?'). This is what places
    online-first-2018 articles into their real 2019 (vol 89) issue."""
    try:
        return int(vol) + 1930
    except (ValueError, TypeError):
        return yr(doi)

def load_prog():
    if PROG.exists():
        p = json.loads(PROG.read_text(encoding="utf-8"))
    else:
        p = {}
    p.setdefault("downloaded", {}); p.setdefault("failed", [])
    return p

def save_prog(p): PROG.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")

def fpath(doi, vol, iss):
    return DL/f"V{safe(str(vol))}I{safe(str(iss))}"/f"{safe(doi)}.pdf"

def valid_pdf(fp):
    try:
        return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception:
        return False

def _int(x):
    try: return int(x)
    except Exception: return 10**9   # unknown vol/issue ("?") sorts last

def build_work():
    work = []
    for k, arts in DOIS["issues"].items():
        for a in arts:
            vol = a.get("volume", "?"); iss = a.get("issue", "?")
            y = art_year(vol, a["doi"])   # issue year (vol-based), NOT online-first DOI year
            if not (2019 <= y <= MAX_YEAR):   # skip 2018 vol (already in Drive) + beyond cap
                continue
            fp = fpath(a["doi"], vol, iss)
            if valid_pdf(fp):
                continue
            work.append((a["doi"], vol, iss, y))
    # strict order: year -> volume -> issue -> doi, so each year finishes before the next
    work.sort(key=lambda w: (w[3], _int(w[1]), _int(w[2]), w[0]))
    return [(d, v, i) for (d, v, i, y) in work]

async def wait_cf(page, m=25):
    for _ in range(m):
        try:
            if "just a moment" not in (await page.title()).lower():
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False

async def dl_one(page, doi, vol, iss):
    fp = fpath(doi, vol, iss); fp.parent.mkdir(exist_ok=True)
    url = f"https://www.tandfonline.com/doi/pdf/{doi}?download=true"
    for attempt in range(3):
        try:
            async with page.expect_download(timeout=60000) as dl_info:
                try:
                    await page.goto(url, timeout=60000)
                except Exception:
                    pass  # "Download is starting" throws by design
            dl = await dl_info.value
            await dl.save_as(str(fp))
            if valid_pdf(fp):
                return True, fp.stat().st_size
        except Exception:
            pass
        await asyncio.sleep(1.5 * (attempt + 1))  # backoff
    return False, 0

async def worker(name, ctx, queue, prog, stats, t0, budget):
    page = await ctx.new_page()
    try:
        while True:
            if time.time() - t0 > budget:
                return
            try:
                doi, vol, iss = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            ok, sz = await dl_one(page, doi, vol, iss)
            if ok:
                prog["downloaded"][doi] = {"vol": vol, "iss": iss, "size": sz}
                stats["ok"] += 1
                tag = f"OK {sz//1024}KB"
            else:
                if doi not in prog["failed"]:
                    prog["failed"].append(doi)
                stats["fail"] += 1
                tag = "FAIL"
            n = stats["ok"] + stats["fail"]
            if n % 5 == 0 or not ok:
                el = time.time() - t0
                print(f"  [{stats['base']+stats['ok']}/{stats['total']}] "
                      f"V{vol}I{iss} {doi[-11:]} {tag} "
                      f"{n/el:.2f}/s ({el:.0f}s)", flush=True)
            if n % 15 == 0:
                save_prog(prog)
            await asyncio.sleep(DELAY)   # rate limit between downloads
    finally:
        await page.close()

async def main(budget):
    prog = load_prog()
    work = build_work()
    base = len([1 for k,arts in DOIS["issues"].items() for a in arts
                if 2019<=art_year(a.get("volume","?"),a["doi"])<=MAX_YEAR
                and valid_pdf(fpath(a["doi"],a.get("volume","?"),a.get("issue","?")))])
    total = base + len(work)
    print(f"gscs20 2019-2026: target={total}, on_disk={base}, queue={len(work)}, conc={CONC}, budget={budget}s")
    from collections import Counter
    qc = Counter(art_year(v, d) for d, v, i in work)
    print("queue by issue-year:", dict(sorted(qc.items())))
    print("queue head:", [(v, i, art_year(v, d)) for d, v, i in work[:3]])
    if not work:
        print("Queue empty — all 2019-2026 downloaded.")
        return
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        warm = await ctx.new_page()
        await warm.goto("https://www.tandfonline.com/", wait_until="domcontentloaded", timeout=45000)
        await wait_cf(warm)
        print("warmup:", (await warm.title())[:60])

        # PRE-FLIGHT: confirm this IP has institutional access before burning a batch.
        probe = await warm.evaluate("""async(url)=>{try{
            const r=await fetch(url,{credentials:'include',redirect:'follow'});
            const b=new Uint8Array(await r.arrayBuffer());
            return {len:b.length, head:String.fromCharCode.apply(null,b.subarray(0,5)),
                    ip:(String.fromCharCode.apply(null,b.subarray(0,4000)).match(/IP address of ([0-9.]+)/)||[])[1]||''};
          }catch(e){return{len:0,head:'',ip:''}}}""",
          "https://www.tandfonline.com/doi/pdf/10.1080/00949655.2021.1946065?download=true")
        egress = await warm.evaluate("async()=>{try{return await (await fetch('https://api.ipify.org')).text();}catch(e){return '?';}}")
        await warm.close()
        if not (probe.get("head") == "%PDF-" and probe.get("len", 0) > 10000):
            print("=" * 64)
            print(f"NO INSTITUTIONAL ACCESS on this IP (egress {egress}).")
            if probe.get("ip"):
                print(f"T&F says IP {probe['ip']} is NOT recognised.")
            print("Reconnect to a recognised JMI/ONOS IP, then re-run. Aborting fast.")
            print("=" * 64)
            return

        queue = asyncio.Queue()
        for item in work:
            queue.put_nowait(item)
        stats = {"ok": 0, "fail": 0, "base": base, "total": total}
        t0 = time.time()
        await asyncio.gather(*[worker(f"w{i}", ctx, queue, prog, stats, t0, budget)
                               for i in range(CONC)])
        save_prog(prog)
        el = time.time() - t0
        print(f"\nBatch done in {el:.0f}s: +{stats['ok']} ok, {stats['fail']} fail | "
              f"on_disk now ~{base+stats['ok']}/{total} | remaining ~{len(work)-stats['ok']}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=540)
    ap.add_argument("--delay", type=float, default=DELAY)
    ap.add_argument("--max-year", type=int, default=MAX_YEAR)
    a = ap.parse_args()
    DELAY = a.delay
    MAX_YEAR = a.max_year
    asyncio.run(main(a.budget))
