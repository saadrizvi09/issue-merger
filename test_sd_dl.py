"""Debug: log every response during the Elsevier pdfft navigation to find the PDF. CDP 9224."""
import asyncio, re
from pathlib import Path
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9224"
BASE = "https://www-sciencedirect-com.jmi.mapmyaccess.com"
PII = "S0377025718302805"
OUT = Path("C:/Projects/Automate pdf merge journal/_sd_test.pdf")

async def main():
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        logs = []
        got = {"data": None, "url": None}
        async def on_resp(resp):
            u = resp.url
            ct = resp.headers.get("content-type", "")
            logs.append((resp.status, ct[:25], u[:90]))
            if "pdf" in ct.lower() or (".pdf" in u and "assets" in u):
                try:
                    b = await resp.body()
                    if b[:5] == b"%PDF-":
                        got["data"] = b; got["url"] = u
                except Exception as e:
                    logs.append(("BODYERR", str(e)[:30], u[:60]))
        page.on("response", on_resp)

        await page.goto(f"{BASE}/science/article/pii/{PII}", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)
        html = await page.content()
        m = re.search(r'(/science/article/pii/%s/pdfft\?md5=[^"\'&]+&(?:amp;)?pid=[^"\']+)' % PII, html)
        if not m:
            print("no token link"); await br.close(); return
        pdf_url = BASE + m.group(1).replace("&amp;", "&")
        print("navigating to pdfft token url...")
        logs.clear()
        try:
            async with page.expect_response(lambda r: ".pdf" in r.url and "assets" in r.url, timeout=45000) as ri:
                await page.goto(pdf_url, wait_until="commit", timeout=60000)
            resp = await ri.value
            print("expect_response caught:", resp.status, resp.headers.get("content-type","")[:25], resp.url[:80])
            b = await resp.body()
            print("  body head:", b[:8], "len:", len(b))
            if b[:5] == b"%PDF-":
                OUT.write_bytes(b); print(f"  SAVED {len(b)} -> {OUT}")
        except Exception as e:
            print("expect_response err:", str(e)[:80])
        await asyncio.sleep(3)
        print("\n=== all responses during pdfft nav ===")
        for st, ct, u in logs[-25:]:
            print(f"  {st} {ct} {u}")
        if got["data"]:
            OUT.write_bytes(got["data"]); print(f"\nlistener SAVED {len(got['data'])} from {got['url'][:70]}")
        await br.close()

asyncio.run(main())
