"""Verify Elsevier access via proxy (logged in) + inspect the pdfft PDF mechanic. CDP 9224."""
import asyncio, re
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9224"
BASE = "https://www-sciencedirect-com.jmi.mapmyaccess.com"
PII = "S0377025718302805"

FETCH_JS = """async(u)=>{const ac=new AbortController();const to=setTimeout(()=>ac.abort(),60000);try{
    const r=await fetch(u,{credentials:'include',redirect:'follow',headers:{'Accept':'*/*'},signal:ac.signal});
    const b=new Uint8Array(await r.arrayBuffer());clearTimeout(to);let s='';const C=0x8000;
    for(let i=0;i<b.length;i+=C)s+=String.fromCharCode.apply(null,b.subarray(i,Math.min(i+C,b.length)));
    return {status:r.status,len:b.length,head:String.fromCharCode.apply(null,b.subarray(0,8)),url:r.url,ct:r.headers.get('content-type')||'',body:s.slice(0,4000)};
  }catch(e){clearTimeout(to);return{status:0,len:0,head:'',url:''+e,ct:'',body:''}}}"""

async def main():
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP)
        ctx = br.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        # 1) article page — access check
        await page.goto(f"{BASE}/science/article/pii/{PII}", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)
        print("article title:", (await page.title())[:70])
        print("article url:", page.url[:80])
        html = await page.content()
        for kw in ["Download PDF", "View PDF", "Purchase", "Get Access", "Check access",
                   "access through your", "Sign in", "institutional"]:
            c = html.lower().count(kw.lower())
            if c: print(f"  '{kw}': {c}")
        # find the pdf link in the page (Elsevier embeds it)
        m = re.search(r'"pdfDownload"[^}]*?"linkToPdf"\s*:\s*"([^"]+)"', html)
        if not m:
            m = re.search(r'(/science/article/pii/%s/pdf[^"\']*)' % PII, html)
        print("pdf link in page:", (m.group(1)[:120] if m else "NONE FOUND"))
        # 2) fetch the pdfft intermediate
        print("\n--- fetch /pdfft ---")
        r = await page.evaluate(FETCH_JS, f"{BASE}/science/article/pii/{PII}/pdfft")
        print(f"status={r['status']} ct={r['ct'][:30]} len={r['len']} head={r['head']!r} url={r['url'][:80]}")
        if not r["head"].startswith("%PDF"):
            # look for a real pdf url in the intermediate html/js
            body = r["body"]
            for pat in [r'https?://[^"\'\\]+\.pdf[^"\'\\]*', r'window\.location\s*=\s*[\'"]([^\'"]+)', r'"(https?://[^"]+md5=[^"]+)"']:
                found = re.findall(pat, body)
                if found:
                    print("  candidate PDF url:", found[0][:150])
                    break
            print("  body snippet:", body[:500].replace("\n"," "))
        await br.close()

asyncio.run(main())
