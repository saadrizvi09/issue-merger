"""
Quick test: Reuse CF-warmed page to access T&F articles.
"""
import asyncio, base64, re
from playwright.async_api import async_playwright
from pathlib import Path

DOI = "10.1080/00949655.2018.1430801"
PROJECT = Path("C:/Projects/Automate pdf merge journal")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        page = await ctx.new_page()

        # Warm up: get CF cookies on the homepage
        print("1. Warming up on journal homepage...")
        await page.goto("https://www.tandfonline.com/journals/gscs20",
                        wait_until="domcontentloaded", timeout=30000)
        for i in range(15):
            t = await page.title()
            if "just a moment" not in t.lower():
                print(f"   CF passed after {i+1}s: {t[:80]}")
                break
            await asyncio.sleep(1)
        else:
            print("   CF BLOCKED on homepage")

        # Now try the article page WITH THE SAME PAGE
        print(f"\n2. Same page -> article: {DOI}")
        await page.goto(f"https://doi.org/{DOI}", wait_until="domcontentloaded", timeout=30000)
        for i in range(15):
            t = await page.title()
            if "just a moment" not in t.lower():
                print(f"   CF passed after {i+1}s: {t[:80]}")
                break
            await asyncio.sleep(1)
        else:
            print(f"   CF BLOCKED after article redirect. Title: {await page.title()}")
            print(f"   URL: {page.url}")

        # Also try with a new page in the same context (should share cookies)
        print(f"\n3. New page (same context) -> article: {DOI}")
        page2 = await ctx.new_page()
        await page2.goto(f"https://doi.org/{DOI}", wait_until="domcontentloaded", timeout=30000)
        for i in range(15):
            t = await page2.title()
            if "just a moment" not in t.lower():
                print(f"   CF passed after {i+1}s: {t[:80]}")
                break
            await asyncio.sleep(1)
        else:
            print(f"   CF BLOCKED. Title: {await page2.title()}")
            print(f"   URL: {page2.url}")

        # Check what the page looks like
        t = await page2.title()
        print(f"\n4. Article page analysis:")
        print(f"   Title: {t}")
        print(f"   URL: {page2.url}")

        if "just a moment" in t.lower():
            print("   Still on Cloudflare page - cannot proceed")
        else:
            html = await page2.content()
            for kw in ["View PDF", "Download citation", "Access restricted", "Log in",
                       "Subscribe", "Open access", "citation_pdf_url"]:
                if kw.lower() in html.lower():
                    print(f"   '{kw}': {html.lower().count(kw.lower())}")

            pdf_urls = re.findall(r'citation_pdf_url["\']?\s*content=["\']([^"\']+)["\']', html)
            if pdf_urls:
                print(f"   PDF meta: {pdf_urls}")

            # Try PDF fetch
            pdf_url = f"https://www.tandfonline.com/doi/pdf/{DOI}"
            print(f"\n5. Trying PDF fetch: {pdf_url}")
            resp = await page2.goto(pdf_url, wait_until="commit", timeout=30000)
            if resp:
                print(f"   Status: {resp.status}")
                print(f"   Content-Type: {resp.headers.get('content-type', '?')}")
                body = await resp.body()
                print(f"   Size: {len(body)}")
                print(f"   Is PDF: {body[:5] == b'%PDF-'}")
                print(f"   First bytes: {body[:100]}")
                if body[:5] == b'%PDF-':
                    out = PROJECT / "test_article.pdf"
                    out.write_bytes(body)
                    print(f"   SAVED: {out}")

        await browser.close()

asyncio.run(main())
