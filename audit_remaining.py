"""Audit the remaining IOP CM 2026 articles: 404 (unreachable) vs downloadable."""
import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9223"
BASE = "https://iopscience-iop-org.jmi.mapmyaccess.com"

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))

data = json.loads(Path("iop_0953-8984_dois.json").read_text(encoding="utf-8"))
dl = Path("iop_cm_downloads")
remaining = []
for k, arts in data["issues"].items():
    for a in arts:
        y = a.get("year", 0)
        if not (2023 <= y <= 2026):
            continue
        fp = dl / f"V{safe(a.get('volume','?'))}I{safe(a.get('issue','?'))}" / f"{safe(a['doi'])}.pdf"
        if not (fp.exists() and fp.stat().st_size > 10000):
            remaining.append((a["doi"], y, a.get("volume","?"), a.get("issue","?")))

print(f"remaining total: {len(remaining)}")
# test a sample (up to 15)
sample = remaining[:15]

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
        page = await ctx.new_page()
        await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1)
        n404 = npdf = nother = 0
        for doi, y, v, i in sample:
            r = await page.evaluate(FETCH_JS, f"{BASE}/article/{doi}/pdf")
            st = r.get("status"); ispdf = r.get("head") == "%PDF-" and r.get("len",0) > 10000
            login = "mapmyaccess.com/login" in (r.get("url") or "")
            tag = "PDF" if ispdf else ("404" if st == 404 else ("LOGIN" if login else f"other({st})"))
            if ispdf: npdf += 1
            elif st == 404: n404 += 1
            else: nother += 1
            print(f"  {y} V{v}I{i} {doi[-10:]}: {tag} (len={r.get('len')})")
        print(f"\nSAMPLE: {npdf} downloadable, {n404} are 404 (unreachable), {nother} other")
        await page.close()
        await br.close()

asyncio.run(main())
