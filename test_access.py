"""Quick diagnostic: fetch 2026 PDFs through the live CDP Chrome and report what comes back."""
import asyncio
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9223"
BASE = "https://iopscience-iop-org.jmi.mapmyaccess.com"
DOIS = [
    "10.1088/1361-648x/ae8c09",
    "10.1088/1361-648x/ae86a6",
    "10.1088/1361-648x/ae8d6c",
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        for doi in DOIS:
            url = f"{BASE}/article/{doi}/pdf"
            try:
                resp = await page.goto(url, timeout=45000, wait_until="commit")
                status = resp.status if resp else "?"
                ctype = resp.headers.get("content-type", "?") if resp else "?"
                print(f"DOI {doi}: HTTP {status} | ctype={ctype[:30]} | url={page.url[:65]}")
            except Exception as e:
                print(f"DOI {doi}: ERROR {e}")
        await browser.close()

asyncio.run(main())
