"""
Sci-Hub PDF downloader via Playwright — handles ALTCHA captcha.
Journal of Statistical Computation and Simulation (gscs20)
ISSN 0094-9655, 2018-2026
"""
import asyncio
import sys
import re
import time
from pathlib import Path

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DOWNLOADS = PROJECT / "gscs20_downloads"
DOWNLOADS.mkdir(exist_ok=True)
SCI_HUB = "https://sci-hub.su"

async def solve_captcha(page, max_wait=30):
    """Handle Sci-Hub ALTCHA 'Are you a robot?' page."""
    # Wait a moment for the page to render
    await asyncio.sleep(2)

    # Check page title or content for captcha
    title = await page.title()
    if "robot" not in title.lower():
        # Also check for the answer button
        answer_btn = page.locator('.answer')
        try:
            if await answer_btn.is_visible(timeout=3000):
                pass  # captcha present
            else:
                return True  # no captcha
        except:
            return True  # no captcha

    print("  [captcha] 'Are you a robot?' detected, solving...")

    # Click the "No" button
    answer_btn = page.locator('.answer')
    await answer_btn.click()
    print("  [captcha] Clicked 'No', waiting for ALTCHA proof-of-work...")

    # Wait for the verification to complete and page to reload
    # ALTCHA computes proof-of-work then auto-submits
    for i in range(max_wait):
        await asyncio.sleep(1)
        title = await page.title()
        if "robot" not in title.lower():
            # Also check we're not still on captcha
            answer_btn2 = page.locator('.answer')
            try:
                if await answer_btn2.is_visible(timeout=1000):
                    continue  # still on captcha
            except:
                pass
            print(f"  [captcha] Solved after {i+1}s")
            return True

        # Check for the verification text
        result = page.locator('.result')
        try:
            if await result.is_visible(timeout=500):
                text = await result.inner_text()
                if "Verifying" in text:
                    if i % 3 == 0:
                        print(f"  [captcha] Still verifying... ({i+1}s)")
        except:
            pass

    print(f"  [captcha] WARNING: Timed out after {max_wait}s")
    return False


async def get_pdf_url(page):
    """Extract PDF URL from Sci-Hub article page."""
    await asyncio.sleep(2)

    # Try embed tag first (most common)
    embed = page.locator('embed[type="application/pdf"]')
    try:
        if await embed.count(timeout=3000) > 0:
            src = await embed.first.get_attribute('src')
            if src:
                return src
    except:
        pass

    # Try iframe
    iframe = page.locator('iframe')
    try:
        if await iframe.count(timeout=3000) > 0:
            src = await iframe.first.get_attribute('src')
            if src:
                return src
    except:
        pass

    # Try #pdf element
    pdf_el = page.locator('#pdf')
    try:
        if await pdf_el.count(timeout=3000) > 0:
            src = await pdf_el.get_attribute('src')
            if src:
                return src
    except:
        pass

    # Search page source for PDF URLs
    content = await page.content()
    pdf_matches = re.findall(r'(https?://[^"\']+\.pdf[^"\']*)', content)
    if pdf_matches:
        return pdf_matches[0]

    # Search for sci-hub specific patterns (tree.html redirects, etc.)
    tree_matches = re.findall(r'(//[^"\']+tree[^"\']+)', content)
    if tree_matches:
        return 'https:' + tree_matches[0]

    return None


async def download_article(page, doi: str):
    """Download a single article via Sci-Hub."""
    doi_url = f"{SCI_HUB}/{doi}"
    print(f"  [{doi[:40]}...] Loading...")

    await page.goto(doi_url, wait_until="domcontentloaded", timeout=60000)

    # Handle captcha
    if not await solve_captcha(page):
        print(f"  [{doi[:40]}...] ✗ Captcha unsolved")
        return None

    # Get PDF URL
    pdf_url = await get_pdf_url(page)
    if not pdf_url:
        # Try reloading once
        print(f"  [{doi[:40]}...] No PDF found, retrying...")
        await asyncio.sleep(3)
        pdf_url = await get_pdf_url(page)

    if not pdf_url:
        # Check if we're on the captcha page again
        title = await page.title()
        print(f"  [{doi[:40]}...] ✗ No PDF (title: {title[:80]})")
        return None

    # Make absolute
    if pdf_url.startswith('//'):
        pdf_url = 'https:' + pdf_url
    elif pdf_url.startswith('/'):
        pdf_url = SCI_HUB + pdf_url

    print(f"  [{doi[:40]}...] PDF: {pdf_url[:120]}")

    # Download PDF
    try:
        response = await page.goto(pdf_url, wait_until="commit", timeout=60000)
        if response and response.ok:
            body = await response.body()
            if body[:5] == b'%PDF-':
                return body
            else:
                print(f"  [{doi[:40]}...] ✗ Not PDF (starts: {body[:30]})")
                return None
        else:
            status = response.status if response else 'no response'
            print(f"  [{doi[:40]}...] ✗ HTTP {status}")
            return None
    except Exception as e:
        print(f"  [{doi[:40]}...] ✗ Download error: {e}")
        return None


async def test_single(doi: str):
    """Test Sci-Hub with one DOI."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            # First visit home to warm up session
            print("Warming up Sci-Hub session...")
            await page.goto(SCI_HUB, wait_until="domcontentloaded", timeout=60000)
            await solve_captcha(page)

            result = await download_article(page, doi)
            if result:
                safe_name = doi.replace('/', '_').replace(':', '_')
                pdf_path = DOWNLOADS / f"{safe_name}.pdf"
                pdf_path.write_bytes(result)
                print(f"✓ Saved {len(result)} bytes → {pdf_path.name}")
                return str(pdf_path)
            else:
                print(f"✗ Failed to download {doi}")
                return None
        finally:
            await browser.close()


async def main():
    doi = sys.argv[1] if len(sys.argv) > 1 else "10.1080/00949655.2024.2382295"
    print(f"Sci-Hub Playwright test — {doi}")
    result = await test_single(doi)
    if result:
        print(f"\nSUCCESS: {result}")
    else:
        print("\nFAILED")

if __name__ == "__main__":
    asyncio.run(main())
