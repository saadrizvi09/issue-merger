"""Test MapMyAccess proxy access + whether a login is needed right now."""
import asyncio
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9223"
BASE = "https://iopscience-iop-org.jmi.mapmyaccess.com"
DOI = "10.1088/1361-648x/ae8c09"

FETCH_JS = """async(u)=>{try{
    const r=await fetch(u,{credentials:'include',redirect:'follow',headers:{'Accept':'application/pdf,*/*'}});
    const b=new Uint8Array(await r.arrayBuffer());
    const head=String.fromCharCode.apply(null,b.subarray(0,5));
    return {status:r.status,len:b.length,head:head,url:r.url};
  }catch(e){return{status:0,len:0,head:'',url:''+e}}}"""

async def main():
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        # Use a SEPARATE throwaway page so we never disturb the user's login tab.
        page = await ctx.new_page()
        try:
            await page.goto(BASE + "/journal/0953-8984", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2)
            print("proxy nav title:", (await page.title())[:70])
            print("proxy nav url:", page.url[:90])
        except Exception as e:
            print("nav err:", e)
        try:
            r = await page.evaluate(FETCH_JS, f"{BASE}/article/{DOI}/pdf")
            is_pdf = r.get("head") == "%PDF-" and r.get("len",0) > 10000
            login = "mapmyaccess.com/login" in (r.get("url") or "")
            print(f"PDF fetch: HTTP {r.get('status')} len={r.get('len')} pdf={is_pdf} login_redirect={login} url={r.get('url','')[:80]}")
        finally:
            await page.close()

asyncio.run(main())
