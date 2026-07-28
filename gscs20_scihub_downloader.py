"""
Comprehensive gscs20 PDF downloader using Playwright.
Approach: Navigate T&F journal pages with a real browser to avoid Cloudflare blocks.
Downloads individual article PDFs per issue.

Journal: Journal of Statistical Computation and Simulation
ISSN: 0094-9655 / 1563-5163
Years: 2018-2026 (volumes 88-96, 18 issues each)
"""
import asyncio
import json
import sys
import time
import re
from pathlib import Path
from datetime import datetime

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DOWNLOADS = PROJECT / "gscs20_downloads"
DOWNLOADS.mkdir(exist_ok=True)
PROGRESS_FILE = PROJECT / "gscs20_progress.json"
DOIS_FILE = PROJECT / "gscs20_dois.json"

SCI_HUB_MIRRORS = [
    "https://sci-hub.su",
    "https://sci-hub.st",
    "https://sci-hub.ru",
]

async def load_progress():
    """Load download progress."""
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"downloaded": [], "failed": [], "issues_done": []}

async def save_progress(progress):
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))

async def solve_scihub_captcha(page):
    """Handle Sci-Hub ALTCHA captcha page."""
    try:
        title = await page.title()
        if "robot" not in title.lower():
            answer_btn = page.locator('.answer')
            try:
                if not await answer_btn.is_visible(timeout=2000):
                    return True
            except:
                return True
    except:
        return True

    print("    [captcha] Solving ALTCHA...")
    try:
        answer_btn = page.locator('.answer')
        await answer_btn.click(timeout=5000)
    except:
        pass

    for i in range(25):
        await asyncio.sleep(1)
        try:
            title = await page.title()
            if "robot" not in title.lower():
                print(f"    [captcha] Solved after {i+1}s")
                return True
        except:
            return True
    return False

async def scihub_download(context, doi: str):
    """Try downloading a single DOI from Sci-Hub."""
    page = await context.new_page()
    try:
        for mirror in SCI_HUB_MIRRORS:
            try:
                url = f"{mirror}/{doi}"
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)

                if not await solve_scihub_captcha(page):
                    continue

                title = await page.title()
                if "no articles" in title.lower() or "article not found" in title.lower():
                    continue

                # Look for PDF embed/iframe
                for selector in ['embed[type="application/pdf"]', '#pdf', '#articlePDF']:
                    el = page.locator(selector)
                    try:
                        if await el.count(timeout=2000) > 0:
                            pdf_url = await el.first.get_attribute('src')
                            if pdf_url:
                                if pdf_url.startswith('//'): pdf_url = 'https:' + pdf_url
                                elif pdf_url.startswith('/'): pdf_url = mirror + pdf_url
                                resp = await page.goto(pdf_url, wait_until="commit", timeout=30000)
                                if resp and resp.ok:
                                    body = await resp.body()
                                    if body[:5] == b'%PDF-':
                                        await page.close()
                                        return body
                    except:
                        pass

                # Search page source for PDF URLs
                content = await page.content()
                pdfs = re.findall(r'(https?://[^"\'\s]+\.pdf[^"\'\s]*)', content)
                for pdf_url in pdfs[:3]:
                    try:
                        resp = await page.goto(pdf_url, wait_until="commit", timeout=30000)
                        if resp and resp.ok:
                            body = await resp.body()
                            if body[:5] == b'%PDF-':
                                await page.close()
                                return body
                    except:
                        pass

            except Exception as e:
                continue

        await page.close()
        return None
    except:
        await page.close()
        return None

async def process_dois(dois_to_download, max_workers=4):
    """Download multiple DOIs from Sci-Hub with concurrency."""
    from playwright.async_api import async_playwright
    import asyncio

    progress = await load_progress()
    downloaded_set = set(progress["downloaded"])
    failed_set = set(progress["failed"])

    # Filter out already handled
    to_download = [(doi, info) for doi, info in dois_to_download
                   if doi not in downloaded_set and doi not in failed_set]

    if not to_download:
        print("All DOIs already processed.")
        return progress

    print(f"Downloading {len(to_download)} articles using up to {max_workers} workers...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        semaphore = asyncio.Semaphore(max_workers)

        async def download_one(doi, info):
            async with semaphore:
                vol = info.get('volume', '?')
                iss = info.get('issue', '?')
                title = (info.get('title', '?'))[:80]
                print(f"  [V{vol}I{iss}] {doi[:50]}... - {title}")

                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                )
                try:
                    pdf_data = await scihub_download(context, doi)
                    if pdf_data:
                        progress["downloaded"].append(doi)
                        return ("success", doi, pdf_data, vol, iss)
                    else:
                        progress["failed"].append(doi)
                        return ("failed", doi, None, vol, iss)
                finally:
                    await context.close()

        tasks = [download_one(doi, info) for doi, info in to_download]
        results = await asyncio.gather(*tasks)
        await browser.close()

    # Save progress
    await save_progress(progress)

    # Organize PDFs by issue
    for result in results:
        status, doi, pdf_data, vol, iss = result
        if status == "success" and pdf_data:
            issue_dir = DOWNLOADS / f"V{vol}I{iss}"
            issue_dir.mkdir(exist_ok=True)
            safe_name = doi.replace('/', '_').replace(':', '_')
            pdf_path = issue_dir / f"{safe_name}.pdf"
            pdf_path.write_bytes(pdf_data)
            print(f"    Saved: {pdf_path.name} ({len(pdf_data)} bytes)")

    success = len([r for r in results if r[0] == "success"])
    failed = len([r for r in results if r[0] == "failed"])
    print(f"\nResults: {success} success, {failed} failed")
    return progress

def main():
    if not DOIS_FILE.exists():
        print(f"ERROR: {DOIS_FILE} not found. Run collect_gscs20_dois.py first.")
        sys.exit(1)

    data = json.loads(DOIS_FILE.read_text())
    issues = data.get("issues", {})

    # Flatten all articles into doi list
    all_articles = []
    for issue_key, articles in sorted(issues.items()):
        for art in articles:
            doi = art.get("doi")
            if doi:
                all_articles.append((doi, {
                    "volume": art.get("volume", "?"),
                    "issue": art.get("issue", "?"),
                    "title": art.get("title", "?"),
                    "page": art.get("page", "?"),
                }))

    print(f"Total articles: {len(all_articles)}")
    print(f"Total issues: {len(issues)}")

    # Test mode
    if "--dry-run" in sys.argv:
        print("\n=== DRY RUN - showing first 10 articles ===")
        for doi, info in all_articles[:10]:
            print(f"  {doi} - V{info['volume']}I{info['issue']}: {info['title'][:80]}")
        return

    # Limit for testing
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])
        all_articles = all_articles[:limit]
        print(f"Limited to {limit} articles")

    asyncio.run(process_dois(all_articles))

if __name__ == "__main__":
    main()
