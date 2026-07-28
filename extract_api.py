"""Extract T&F API endpoints from the journal homepage using Playwright."""
import asyncio, json, re
from playwright.async_api import async_playwright

async def extract_api():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # Intercept network requests to find API calls
        api_requests = []
        def on_request(request):
            url = request.url
            if any(x in url.lower() for x in ['api', 'loi', '/toc/', '/action/', 'literatum']):
                api_requests.append(url)

        page.on('request', on_request)

        print("Loading T&F journal homepage...")
        await page.goto("https://www.tandfonline.com/journals/gscs20", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        print(f"\nAPI-like requests captured ({len(api_requests)}):")
        for u in api_requests:
            print(f"  {u[:200]}")

        # Also extract from page source
        html = await page.content()

        # Look for script data / config
        for pattern in [r'(/action/[^"\']+)', r'"(/toc/[^"\']+)"', r'"(/loi/[^"\']+)"',
                       r'api[^"\']*journals[^"\']*', r'literatum[^"\']*']:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                print(f"\nPattern '{pattern}': {matches[:10]}")

        # Save full HTML for analysis
        from pathlib import Path
        Path("gscs20_homepage.html").write_text(html, encoding='utf-8')
        print(f"\nSaved HTML ({len(html):,} bytes)")

        await browser.close()

asyncio.run(extract_api())
