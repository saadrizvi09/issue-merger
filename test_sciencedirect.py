"""Test ScienceDirect (Elsevier) PDF access — direct JMI IP and via MapMyAccess proxy."""
import asyncio
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9224"
PIIS = ["S0377025718302805", "S037702571830137X"]

FETCH_JS = """async(u)=>{try{
    const r=await fetch(u,{credentials:'include',redirect:'follow',headers:{'Accept':'application/pdf,*/*'}});
    const b=new Uint8Array(await r.arrayBuffer());
    const head=String.fromCharCode.apply(null,b.subarray(0,8));
    return {status:r.status,len:b.length,head:head,url:r.url,ct:r.headers.get('content-type')||''};
  }catch(e){return{status:0,len:0,head:'',url:''+e,ct:''}}}"""

async def probe(page, label, base):
    print(f"\n--- {label}: {base} ---")
    try:
        await page.goto(base + "/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2)
        print("  landing title:", (await page.title())[:60])
    except Exception as e:
        print("  nav err:", e)
    egress = await page.evaluate("async()=>{try{return await (await fetch('https://api.ipify.org')).text()}catch(e){return '?'}}")
    print("  egress IP:", egress)
    for pii in PIIS:
        for suffix in ["/science/article/pii/%s/pdfft" % pii, "/science/article/pii/%s/pdf" % pii]:
            r = await page.evaluate(FETCH_JS, base + suffix)
            is_pdf = r.get("head","").startswith("%PDF")
            print(f"  {pii} {suffix.split('/')[-1]}: HTTP {r.get('status')} ct={r.get('ct','')[:25]} len={r.get('len')} pdf={is_pdf} url={r.get('url','')[:60]}")
            if is_pdf:
                return True
    return False

async def main():
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await probe(page, "DIRECT", "https://www.sciencedirect.com")
        await probe(page, "PROXY", "https://www-sciencedirect-com.jmi.mapmyaccess.com")
        await br.close()

asyncio.run(main())
