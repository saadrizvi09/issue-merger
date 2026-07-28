"""
Quick debug: test Sci-Hub with detailed page analysis
"""
import asyncio
import sys
from pathlib import Path

PROJECT = Path("C:/Projects/Automate pdf merge journal")
SCI_HUB = "https://sci-hub.su"

async def debug():
    from playwright.async_api import async_playwright
    doi = sys.argv[1] if len(sys.argv) > 1 else "10.1080/00949655.2018.1514019"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # Go directly to DOI
        doi_url = f"{SCI_HUB}/{doi}"
        print(f"Loading: {doi_url}")
        await page.goto(doi_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        title = await page.title()
        print(f"Page title: {title}")
        print(f"Current URL: {page.url}")

        # Handle captcha if needed
        if "robot" in title.lower():
            print("Captcha detected, solving...")
            answer_btn = page.locator('.answer')
            try:
                await answer_btn.click(timeout=5000)
                print("Clicked 'No', waiting...")
                for i in range(20):
                    await asyncio.sleep(1)
                    t = await page.title()
                    if "robot" not in t.lower():
                        print(f"Captcha solved after {i+1}s, new title: {t}")
                        break
                else:
                    print("Captcha may not have solved")
            except Exception as e:
                print(f"Error clicking: {e}")

        # Now analyze the page
        title = await page.title()
        print(f"\nAfter captcha - Title: {title}")
        print(f"URL: {page.url}")

        # Save full HTML for analysis
        html = await page.content()
        html_path = PROJECT / "scihub_page.html"
        html_path.write_text(html, encoding='utf-8')
        print(f"Saved HTML ({len(html)} bytes) to {html_path}")

        # Save screenshot
        await page.screenshot(path=str(PROJECT / "scihub_after.png"), full_page=True)
        print("Saved screenshot to scihub_after.png")

        # Search for PDF indicators
        for pattern in ['embed', 'iframe', 'pdf', 'PDF', 'download', 'button', 'src=']:
            count = html.count(pattern)
            if count > 0:
                print(f"'{pattern}' found {count} times")

        # Search for PDF URLs
        import re
        pdfs = re.findall(r'(?:src|href)=["\']([^"\']*\.pdf[^"\']*)["\']', html)
        print(f"PDF URLs in HTML: {pdfs}")

        # Also check for sci-hub download button patterns
        saves = re.findall(r'(?:save|download|open)', html, re.IGNORECASE)
        print(f"Download-related words: {len(saves)}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug())
