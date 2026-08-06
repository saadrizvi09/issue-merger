"""
Downloader for J. Non-Newtonian Fluid Mechanics (Elsevier/ScienceDirect) via MapMyAccess proxy.
Mechanic (proven): article page (challenge cookies + tokenized pdf link) -> navigate to the
/pdfft?md5=...&pid=... token URL -> Elsevier solves the JS challenge, redirects to
pdf.sciencedirectassets.com, Chrome's PDF viewer loads it -> a response listener captures the
application/pdf body. Keeps a warm Chrome (no Radware here; challenge cookies persist).

Usage: python jnnfm_download.py --budget 480
"""
import asyncio, re, sys, json, time, argparse
from pathlib import Path
from playwright.async_api import async_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--manifest", default="jnnfm_0377-0257_dois.json")
ap.add_argument("--dldir", default="jnnfm_downloads")
ap.add_argument("--base", default="https://www-sciencedirect-com.jmi.mapmyaccess.com")
ap.add_argument("--cdp", default="http://127.0.0.1:9224")
ap.add_argument("--min-year", type=int, default=2018)
ap.add_argument("--max-year", type=int, default=2026)
ap.add_argument("--delay", type=float, default=2.0)
ap.add_argument("--budget", type=int, default=480)
A = ap.parse_args()

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DL = PROJECT / A.dldir
DL.mkdir(exist_ok=True)
DOIS = json.loads((PROJECT / A.manifest).read_text(encoding="utf-8"))

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def _int(x):
    try: return int(x)
    except Exception: return 10**9
def fpath(doi, vol, iss): return DL / f"V{safe(vol)}I{safe(iss)}" / f"{safe(doi)}.pdf"
def valid_pdf(fp):
    try: return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception: return False

def build_work():
    work = []
    for k, arts in DOIS["issues"].items():
        for a in arts:
            y = a.get("year", 0)
            if not (A.min_year <= y <= A.max_year): continue
            if not a.get("pii"): continue
            vol = a.get("volume", "?"); iss = a.get("issue", "?")
            if valid_pdf(fpath(a["doi"], vol, iss)): continue
            work.append((a["pii"], a["doi"], vol, iss, y))
    work.sort(key=lambda w: (w[4], _int(w[2]), _int(w[3]), w[1]))
    return work

async def dl_one(ctx, pii):
    """Return (bytes|None, note). Uses a FRESH page per article so the previous article's
    PDF-viewer state can't bleed into this one (that carryover made every article after the
    first fail)."""
    page = await ctx.new_page()
    got = {"data": None}
    async def on_resp(resp):
        try:
            u = resp.url
            ct = resp.headers.get("content-type", "")
            if "application/pdf" in ct.lower() or (".pdf" in u and "sciencedirectassets" in u):
                b = await resp.body()
                if b[:5] == b"%PDF-" and len(b) > 10000:
                    got["data"] = b
        except Exception:
            pass
    page.on("response", on_resp)
    try:
        try:
            await asyncio.wait_for(page.goto(f"{A.base}/science/article/pii/{pii}",
                                             wait_until="domcontentloaded", timeout=45000), timeout=50)
        except Exception:
            return None, "article-timeout"
        # The tokenized pdfft link is injected by JS after load — poll for it (up to ~9s).
        pat = re.compile(r'(/science/article/pii/%s/pdfft\?md5=[^"\'&]+&(?:amp;)?pid=[^"\']+)' % re.escape(pii))
        m = None
        for _ in range(18):
            html = await page.content()
            m = pat.search(html)
            if m:
                break
            await asyncio.sleep(0.5)
        if not m:
            if "get access" in html.lower() or "purchase pdf" in html.lower() or "checkout" in html.lower():
                return None, "no-access"
            return None, "no-token"
        pdf_url = A.base + m.group(1).replace("&amp;", "&")
        # Navigate to the token URL and let the PDF viewer load — the challenge resolves,
        # redirects to the CDN PDF, and the viewer's application/pdf response (caught by the
        # listener) carries the bytes. The Elsevier challenge is flaky, so retry a few times;
        # re-fetching the article page in between refreshes the challenge token.
        for attempt in range(2):
            # commit (return as soon as navigation starts) — the challenge resolves and the
            # CDN application/pdf response fires while we poll; we DON'T wait for the viewer to
            # finish rendering, which is the slow part (esp. for large PDFs).
            try:
                await asyncio.wait_for(page.goto(pdf_url, wait_until="commit", timeout=45000), timeout=50)
            except Exception:
                pass
            for _ in range(50):        # up to ~25s for the pdf response to arrive
                if got["data"]:
                    break
                await asyncio.sleep(0.5)
            if got["data"]:
                return got["data"], "ok"
            if attempt == 0:
                # refresh the challenge token once: revisit article page, re-extract
                try:
                    await asyncio.wait_for(page.goto(f"{A.base}/science/article/pii/{pii}",
                                                     wait_until="domcontentloaded", timeout=45000), timeout=50)
                    await asyncio.sleep(0.8)
                    html = await page.content()
                    m2 = pat.search(html)
                    if m2:
                        pdf_url = A.base + m2.group(1).replace("&amp;", "&")
                except Exception:
                    pass
        return None, "no-pdf"
    finally:
        try: await page.close()
        except Exception: pass

async def main():
    work = build_work()
    total = sum(1 for k,arts in DOIS["issues"].items() for a in arts
                if a.get("pii") and A.min_year <= a.get("year",0) <= A.max_year)
    on_disk = total - len(work)
    print(f"JNNFM {DOIS['issn']} {A.min_year}-{A.max_year}: target={total}, on_disk={on_disk}, "
          f"queue={len(work)}, delay={A.delay}, budget={A.budget}s", flush=True)
    if not work:
        print("Queue empty — all done."); return
    from collections import Counter
    print("queue by year:", dict(sorted(Counter(w[4] for w in work).items())), flush=True)

    t0 = time.time()
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(A.cdp)
        ctx = br.contexts[0]
        wp = ctx.pages[0] if ctx.pages else await ctx.new_page()
        # warmup: ensure logged-in SD session
        try:
            await wp.goto(A.base + "/", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(1)
            t = await wp.title()
            print("warmup:", t[:50], flush=True)
            if "login" in wp.url.lower() or "sign in" in t.lower():
                print("NO SESSION — needs MapMyAccess login. Aborting."); return
        except Exception as e:
            print("warmup err:", str(e)[:60])

        ok = fail = 0
        for pii, doi, vol, iss, y in work:
            if time.time() - t0 > A.budget:
                break
            fp = fpath(doi, vol, iss); fp.parent.mkdir(exist_ok=True)
            data, note = await dl_one(ctx, pii)
            if data:
                fp.write_bytes(data)
                if valid_pdf(fp):
                    ok += 1
                    print(f"  [{on_disk+ok}/{total}] V{vol}I{iss} {doi[-12:]} OK {len(data)//1024}KB", flush=True)
                else:
                    fail += 1
            else:
                fail += 1
                print(f"  V{vol}I{iss} {doi[-12:]} FAIL:{note}", flush=True)
            await asyncio.sleep(A.delay)
        print(f"\nBatch {int(time.time()-t0)}s: +{ok} ok, {fail} fail | on_disk ~{on_disk+ok}/{total}", flush=True)
        await br.close()

asyncio.run(main())
