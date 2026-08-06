"""
Auto-login to MapMyAccess (ScienceDirect) via Microsoft/OAuth SSO on port 9224.
Same MMA login as IOP; the 9224 Chrome profile has the cached Microsoft session so the
OAuth click auto-completes. Exit 0 = success, 1 = failed.
"""
import asyncio, sys, time
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9224"
REDIRECT_URL = "https://www-sciencedirect-com.jmi.mapmyaccess.com/journal/journal-of-non-newtonian-fluid-mechanics"
LOGIN_URL = "https://jmi.mapmyaccess.com/login?redirect=https%3A%2F%2Fwww-sciencedirect-com.jmi.mapmyaccess.com%2Fjournal%2Fjournal-of-non-newtonian-fluid-mechanics"
TIMEOUT = 100

async def main():
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        page = await ctx.new_page()
        print("Navigating to ScienceDirect via MMA proxy...", flush=True)
        try:
            await page.goto(REDIRECT_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"Nav error: {e}", flush=True)
        await asyncio.sleep(3)
        title = await page.title(); url = page.url
        print(f"Title: {title!r}  URL: {url[:80]}", flush=True)
        if "sciencedirect" in url.lower() and "login" not in url.lower() and "ScienceDirect" in title:
            print("Already logged in.", flush=True); await page.close(); return True

        print("Not logged in — going to login form...", flush=True)
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"Login page nav error: {e}", flush=True)
        await asyncio.sleep(5)   # MMA login page never reaches networkidle; give JS time to render buttons
        clicked = False
        for sel in ['button.loginAuthBtnHover', 'button[onclick*="OAuth:6799a2a10ff5fb088642fbab"]']:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click(); clicked = True; print(f"  clicked {sel}", flush=True); break
            except Exception:
                pass
        if not clicked:
            try:
                r = await page.evaluate("""() => {
                    const b = Array.from(document.querySelectorAll('button')).find(b => (b.getAttribute('onclick')||'').includes('OAuth'));
                    if (b) { b.click(); return true; } return false; }""")
                if r: clicked = True; print("  JS-clicked OAuth", flush=True)
            except Exception:
                pass
        if not clicked:
            try:
                await page.evaluate('handleLoginOption("OAuth:6799a2a10ff5fb088642fbab")'); clicked = True
            except Exception:
                pass
        if not clicked:
            print("  login button not found", flush=True); await page.close(); return False

        print("Clicked OAuth — waiting for redirect back to ScienceDirect...", flush=True)
        t0 = time.time()
        while time.time() - t0 < TIMEOUT:
            await asyncio.sleep(3)
            try:
                t = await page.title(); u = page.url
                if "sciencedirect" in u.lower() and "login" not in u.lower():
                    print("Login complete!", flush=True); await page.close(); return True
                if "microsoftonline" in u:
                    print("  on Microsoft SSO, waiting...", flush=True)
            except Exception:
                pass
        print("Login timed out.", flush=True); await page.close(); return False

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
