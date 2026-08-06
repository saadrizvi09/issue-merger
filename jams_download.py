"""Download free-archive JAMS PDFs (2018-2020) via in-page fetch from ams.org origin.
Free content, no login/proxy. AMS is behind Cloudflare + rate-limits, so pace gently and
retry on 429. CDP 9224."""
import asyncio, re, sys, json, time, argparse
from pathlib import Path
from playwright.async_api import async_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--cdp", default="http://127.0.0.1:9224")
ap.add_argument("--delay", type=float, default=4.0)
ap.add_argument("--budget", type=int, default=1200)
A = ap.parse_args()

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DL = PROJECT / "jams_downloads"; DL.mkdir(exist_ok=True)
DOIS = json.loads((PROJECT / "jams_0894-0347_dois.json").read_text(encoding="utf-8"))

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def fpath(a): return DL / f"V{safe(a['volume'])}I{safe(a['issue'])}" / f"{safe(a['doi'])}.pdf"
def valid(fp):
    try: return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception: return False

async def dl_one(ctx, pdf_url):
    """Navigate to the PDF URL so Chrome solves Cloudflare's JS challenge, then capture the
    application/pdf response body. fetch() can't pass the challenge; navigation can."""
    page = await ctx.new_page()
    got = {"data": None}
    blocked = {"hit": False}
    async def on_resp(resp):
        try:
            ct = resp.headers.get("content-type", "")
            u = resp.url
            # Fast-fail: if the AMS PDF URL itself returns 429/403 (Cloudflare rate-limit),
            # don't waste 40s polling — bail immediately and move on.
            if u.lower().endswith(".pdf") and "ams.org" in u and resp.status in (429, 403):
                blocked["hit"] = True
                return
            # AMS serves the PDF with a blank/odd content-type, so also match the .pdf URL on
            # ams.org (not the chrome-extension re-serve, whose body isn't retrievable via CDP).
            if "application/pdf" in ct.lower() or (u.lower().endswith(".pdf") and "ams.org" in u):
                b = await resp.body()
                if b[:5] == b"%PDF-" and len(b) > 10000:
                    got["data"] = b
        except Exception:
            pass
    page.on("response", on_resp)
    try:
        try:
            await asyncio.wait_for(page.goto(pdf_url, wait_until="commit", timeout=50000), timeout=55)
        except Exception:
            pass
        # give Cloudflare's challenge time to resolve + the pdf response to arrive.
        # Cap at ~18s: successful loads arrive well under this; anything slower is almost
        # always a failed challenge — bail and let the next pass retry (cheaper than 40s).
        for _ in range(36):   # up to ~18s
            if got["data"] or blocked["hit"]:
                break
            await asyncio.sleep(0.5)
        return got["data"]
    finally:
        try: await page.close()
        except Exception: pass

async def main():
    work = []
    for k, arts in DOIS["issues"].items():
        for a in arts:
            if not valid(fpath(a)): work.append(a)
    total = sum(len(v) for v in DOIS["issues"].values())
    on_disk = total - len(work)
    work.sort(key=lambda a: (int(a["volume"]), int(a["issue"]), a["doi"]))
    print(f"JAMS free 2018-2020: target={total}, on_disk={on_disk}, queue={len(work)}", flush=True)
    if not work:
        print("All done."); return
    t0 = time.time()
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(A.cdp)
        ctx = br.contexts[0]
        wp = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await wp.goto("https://www.ams.org/", wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(2)
        except Exception: pass
        ok = fail = 0
        for a in work:
            if time.time() - t0 > A.budget: break
            fp = fpath(a); fp.parent.mkdir(exist_ok=True)
            # Single attempt per pass — the supervisor loop re-passes failures, so retrying
            # in-line just wastes the pass's budget on stubborn (rate-limited) articles.
            got = await dl_one(ctx, a["pdf_url"])
            if got:
                fp.write_bytes(got)
                if valid(fp):
                    ok += 1
                    print(f"  [{on_disk+ok}/{total}] V{a['volume']}I{a['issue']} {a['doi'][-10:]} OK {len(got)//1024}KB", flush=True)
                else: fail += 1
            else:
                fail += 1
                print(f"  V{a['volume']}I{a['issue']} {a['doi'][-10:]} FAIL", flush=True)
            await asyncio.sleep(A.delay)
        print(f"\nBatch {int(time.time()-t0)}s: +{ok} ok, {fail} fail | on_disk ~{on_disk+ok}/{total}", flush=True)
        await br.close()

asyncio.run(main())
