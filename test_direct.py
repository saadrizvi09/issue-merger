"""Test DIRECT JMI campus-IP access to iopscience.iop.org (no MapMyAccess proxy)."""
import asyncio
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9223"
DIRECT = "https://iopscience.iop.org"
DOIS = [
    "10.1088/1361-648x/ab7f6e",   # old, definitely published
    "10.1088/1361-648x/ae8c09",   # a 2026 article
]

FETCH_JS = """async(u)=>{try{
    const r=await fetch(u,{credentials:'include',redirect:'follow',headers:{'Accept':'application/pdf,*/*'}});
    const b=new Uint8Array(await r.arrayBuffer());
    const head=String.fromCharCode.apply(null,b.subarray(0,5));
    const bot=(r.url||'').includes('perfdrive');
    return {status:r.status,len:b.length,head:head,bot:bot,url:r.url};
  }catch(e){return{status:0,len:0,head:'',bot:false,url:''+e}}}"""

async def main():
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        # must be on iopscience origin for credentialed same-origin fetch
        try:
            await page.goto(DIRECT + "/", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(1)
            print("landing title:", (await page.title())[:60])
        except Exception as e:
            print("nav err:", e)
        egress = await page.evaluate("async()=>{try{return await (await fetch('https://api.ipify.org')).text()}catch(e){return '?'}}")
        print("egress IP:", egress)
        for doi in DOIS:
            r = await page.evaluate(FETCH_JS, f"{DIRECT}/article/{doi}/pdf")
            is_pdf = r.get("head") == "%PDF-" and r.get("len", 0) > 10000
            print(f"DOI {doi}: HTTP {r.get('status')} len={r.get('len')} pdf={is_pdf} bot={r.get('bot')} url={r.get('url','')[:70]}")
        await br.close()

asyncio.run(main())
