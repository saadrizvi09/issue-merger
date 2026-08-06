"""Inspect a ScienceDirect article page to determine access level + PDF link mechanics."""
import asyncio
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9224"
PII = "S0377025718302805"

async def main():
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        url = f"https://www.sciencedirect.com/science/article/pii/{PII}"
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)
        print("title:", (await page.title())[:80])
        print("url:", page.url[:90])
        html = await page.content()
        print("html length:", len(html))
        for kw in ["Download PDF", "View PDF", "Get Access", "Purchase", "purchase",
                   "institutional access", "Check access", "pdfft", "science/article/pii",
                   "Sign in", "access through", "Access through your", "no access", "Full text access"]:
            c = html.lower().count(kw.lower())
            if c:
                print(f"  '{kw}': {c}")
        # look for pdf url in page
        import re
        pdfs = re.findall(r'(https://[^"\']*pdfft[^"\']*)', html)
        if pdfs:
            print("pdfft URLs found:", len(pdfs), pdfs[0][:100])
        # the linkToPdf in embedded JSON
        m = re.search(r'"pdfDownload".{0,400}?"url":"([^"]+)"', html)
        if m:
            print("pdfDownload url:", m.group(1)[:120])
        await br.close()

asyncio.run(main())
