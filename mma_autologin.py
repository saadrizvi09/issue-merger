"""
Auto-login to MapMyAccess via Microsoft/OAuth SSO.
Navigates to MMA login page, clicks the OAuth (Institutional Outlook) button.
The Chrome profile has cached Microsoft session → auto-completes without credentials.
Exit 0 = success. Exit 1 = failed.
"""
import asyncio, sys, time
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9223"
REDIRECT_URL = "https://iopscience-iop-org.jmi.mapmyaccess.com/journal/0953-8984"
LOGIN_URL = "https://jmi.mapmyaccess.com/login?redirect=https%3A%2F%2Fiopscience-iop-org.jmi.mapmyaccess.com%2Fjournal%2F0953-8984"
TIMEOUT = 90

async def main():
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        page = await ctx.new_page()

        # Navigate to the proxied IOP journal page
        print("Navigating to IOP via MMA proxy...", flush=True)
        try:
            await page.goto(REDIRECT_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"Nav error (may redirect to login): {e}", flush=True)

        await asyncio.sleep(3)
        title = await page.title()
        url = page.url
        print(f"Title: {title!r}  URL: {url[:80]}", flush=True)

        # Already logged in?
        if any(x in title for x in ["IOPscience", "Condensed", "Journal", "IOP"]) and "login" not in url:
            print("Already logged in.", flush=True)
            await page.close()
            return True

        # Navigate to login form
        print("Not logged in — going to login form...", flush=True)
        try:
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=20000)
        except Exception as e:
            print(f"Login page nav error: {e}", flush=True)
        await asyncio.sleep(3)

        title = await page.title()
        print(f"Login page title: {title!r}", flush=True)

        # Try multiple strategies to click the OAuth/Microsoft button
        clicked = False

        # Strategy 1: the featured institutional button with class loginAuthBtnHover
        for sel in [
            'button.loginAuthBtnHover',
            'button[onclick*="OAuth:6799a2a10ff5fb088642fbab"]',
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    txt = ((await el.inner_text()) or "").strip()[:40]
                    print(f"  Clicking '{sel}' text='{txt}'", flush=True)
                    await el.click()
                    clicked = True
                    break
            except Exception as e:
                print(f"  Selector '{sel}' err: {e}", flush=True)

        # Strategy 2: JS click on any button whose onclick mentions OAuth
        if not clicked:
            print("  Trying JS click on OAuth button...", flush=True)
            try:
                result = await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const btn = btns.find(b => (b.getAttribute('onclick')||'').includes('OAuth'));
                    if (btn) { btn.click(); return btn.textContent.trim().slice(0,40); }
                    return null;
                }""")
                if result is not None:
                    print(f"  JS click succeeded: '{result}'", flush=True)
                    clicked = True
            except Exception as e:
                print(f"  JS click failed: {e}", flush=True)

        # Strategy 3: call handleLoginOption directly via JS
        if not clicked:
            print("  Calling handleLoginOption directly...", flush=True)
            try:
                await page.evaluate('handleLoginOption("OAuth:6799a2a10ff5fb088642fbab")')
                print("  handleLoginOption called.", flush=True)
                clicked = True
            except Exception as e:
                print(f"  handleLoginOption failed: {e}", flush=True)

        if not clicked:
            print("  All login strategies failed — dumping buttons:", flush=True)
            try:
                btns = await page.evaluate(
                    "()=>Array.from(document.querySelectorAll('button')).map(b=>b.outerHTML.slice(0,120))"
                )
                for b in btns[:10]:
                    print(f"    {b}", flush=True)
            except Exception:
                pass
            await page.close()
            return False

        # Wait for redirect to IOP after OAuth completes
        print("Clicked OAuth — waiting for MMA/IOP redirect...", flush=True)
        t0 = time.time()
        while time.time() - t0 < TIMEOUT:
            await asyncio.sleep(3)
            try:
                t = await page.title()
                u = page.url
                print(f"  title={t[:60]!r}  url={u[:60]!r}", flush=True)
                if any(x in t for x in ["IOPscience", "Condensed", "Journal", "IOP"]):
                    print("Login complete!", flush=True)
                    await page.close()
                    return True
                if "microsoftonline" in u or "login.microsoft" in u:
                    print("  On Microsoft SSO — waiting for auto-complete...", flush=True)
            except Exception:
                pass

        print("Login timed out after 90s.", flush=True)
        await page.close()
        return False

if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
