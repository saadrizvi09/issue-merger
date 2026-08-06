"""AIP Physics of Plasmas (pop, ISSN 1070-664X) downloader, 2018-2026, via JMI/ONOS IP.
Silverchair/Cloudflare-gated + article-pdf redirects cross-origin (so browser fetch is
CORS-blocked). Solution: keep a browser (port 9223) open to hold the Cloudflare
cf_clearance cookie, then download PDFs with concurrent curl (curl follows the redirect,
no CORS). Cookie refreshed by re-navigating the browser. Resume-safe by disk, time-boxed.
Files: aip_pop_downloads/V<vol>I<iss>/<doi>.pdf
"""
import json, re, time, asyncio
from pathlib import Path
from playwright.async_api import async_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DOIS = json.loads((PROJECT/"aip_pop_dois.json").read_text(encoding="utf-8"))
DL = PROJECT/"aip_pop_downloads"; DL.mkdir(exist_ok=True)
CDP = "http://127.0.0.1:9223"
CONC = 4
MIN_YEAR = 2018
MAX_YEAR = 2026
BUDGET = 560
REFRESH_EVERY = 200   # refresh MapMyAccess session cookie every N downloads
PROXY = "https://pubs-aip-org.jmi.mapmyaccess.com"   # MapMyAccess proxy (JMI IP is AIP-blocked; proxy egresses from a different IP)

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def _int(x):
    try: return int(x)
    except Exception: return 10**9
def fpath(doi, vol, iss): return DL/f"V{safe(vol)}I{safe(iss)}"/f"{safe(doi)}.pdf"
def valid_pdf(fp):
    try: return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception: return False

def build_work():
    work = []
    for k, arts in DOIS["issues"].items():
        for a in arts:
            y = a.get("year", 0)
            if not (MIN_YEAR <= y <= MAX_YEAR): continue
            if valid_pdf(fpath(a["doi"], a["volume"], a["issue"])): continue
            work.append((a["doi"], a["pdf"], a["volume"], a["issue"], y))
    work.sort(key=lambda w: (w[4], _int(w[2]), _int(w[3]), w[0]))
    return work

async def refresh_cookies(page):
    # READ-ONLY: just read the existing MapMyAccess session cookies (navigating the proxy
    # page disrupts the session, which broke earlier runs). The login session persists.
    allck = await page.context.cookies()
    cookies = [c for c in allck if "mapmyaccess" in c.get("domain", "")]   # MapMyAccess session
    ua = ""
    for _ in range(4):
        try:
            ua = await page.evaluate("()=>navigator.userAgent"); break
        except Exception:
            await asyncio.sleep(2)
    if not ua:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies), ua

async def curl_pdf(url, fp, cookiestr, ua):
    proc = await asyncio.create_subprocess_exec(
        "curl", "-sL", "--max-time", "45", "-A", ua, "-b", cookiestr, "-o", str(fp), url,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return valid_pdf(fp)

async def dl_one(item, state, sem):
    doi, pdf, vol, iss, y = item
    fp = fpath(doi, vol, iss); fp.parent.mkdir(parents=True, exist_ok=True)
    # route through MapMyAccess proxy. Real filename suffix is either _<doi-suffix>.pdf
    # (newer articles) or _online.pdf (older). Try DOI-suffix first, then _online.
    pp = pdf.replace("https://pubs.aip.org", PROXY)
    if pp.endswith(".pdf"):
        urls = [pp]
    else:
        sfx = doi.split("/")[-1]   # e.g. 5.0270604
        urls = [pp + "_" + sfx + ".pdf", pp + "_online.pdf"]
    async with sem:
        for attempt in range(3):
            for url in urls:
                try:
                    if await curl_pdf(url, fp, state["cookies"], state["ua"]):
                        return True
                except Exception: pass
            await asyncio.sleep(2)
    return False

async def main():
    work = build_work()
    base = sum(1 for k,arts in DOIS["issues"].items() for a in arts
               if MIN_YEAR<=a.get("year",0)<=MAX_YEAR and valid_pdf(fpath(a["doi"],a["volume"],a["issue"])))
    total = base + len(work)
    print(f"AIP pop {MIN_YEAR}-{MAX_YEAR}: target={total}, on_disk={base}, queue={len(work)}, conc={CONC}, budget={BUDGET}s", flush=True)
    if not work:
        print("Queue empty — all done."); return
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP); ctx = br.contexts[0]
        page = await ctx.new_page()
        cookies, ua = await refresh_cookies(page)
        state = {"cookies": cookies, "ua": ua}
        print("cf_clearance present:", "cf_clearance" in cookies, "| cookies len", len(cookies), flush=True)
        sem = asyncio.Semaphore(CONC)
        t0 = time.time(); ok = fail = 0; since_refresh = 0
        i = 0
        while i < len(work) and time.time()-t0 < BUDGET:
            chunk = work[i:i+CONC*3]; i += len(chunk)
            results = await asyncio.gather(*[dl_one(it, state, sem) for it in chunk])
            for r in results:
                if r: ok += 1
                else: fail += 1
            since_refresh += len(chunk)
            el = time.time()-t0
            print(f"  [{base+ok}/{total}] +{ok} ok {fail} fail {ok/el:.2f}/s ({el:.0f}s)", flush=True)
            # refresh cookie periodically OR if this chunk failed badly (cookie likely stale)
            recent_fail = sum(1 for r in results if not r)
            if since_refresh >= REFRESH_EVERY or recent_fail >= max(3, len(chunk)//2):
                cookies, ua = await refresh_cookies(page)
                state["cookies"] = cookies; state["ua"] = ua; since_refresh = 0
        await page.close()
        el = time.time()-t0
        print(f"\nBatch {el:.0f}s: +{ok} ok, {fail} fail | on_disk ~{base+ok}/{total} | remaining ~{len(work)-ok}", flush=True)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-year", type=int, default=MIN_YEAR)
    ap.add_argument("--max-year", type=int, default=MAX_YEAR)
    ap.add_argument("--conc", type=int, default=CONC)
    a = ap.parse_args()
    MIN_YEAR = a.min_year; MAX_YEAR = a.max_year; CONC = a.conc
    asyncio.run(main())
