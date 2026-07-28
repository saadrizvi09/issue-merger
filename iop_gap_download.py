"""Download a targeted list of IOP gap articles (iop2021_gaps.json) via the IOP browser
method (Radware bot-safe: serial, same-origin fetch). Places files at
iop_downloads/V<vol>I<iss>/<doi>.pdf. Uses Chrome CDP on 9222."""
import json, re, time, base64, asyncio
from pathlib import Path
from playwright.async_api import async_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
GAPS = json.loads((PROJECT/"iop2021_gaps.json").read_text(encoding="utf-8"))
DL = PROJECT/"iop_downloads"
CDP = "http://127.0.0.1:9222"
DELAY = 2.5

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def fpath(doi, vol, iss): return DL/f"V{safe(vol)}I{safe(iss)}"/f"{safe(doi)}.pdf"
def valid_pdf(fp):
    try: return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception: return False

FETCH_JS = """async(u)=>{try{
    const r=await fetch(u,{credentials:'include',redirect:'follow',headers:{'Accept':'application/pdf,*/*'}});
    const b=new Uint8Array(await r.arrayBuffer());let s='';const C=0x8000;
    for(let i=0;i<b.length;i+=C)s+=String.fromCharCode.apply(null,b.subarray(i,Math.min(i+C,b.length)));
    const bot=(r.url||'').includes('perfdrive')||(String.fromCharCode.apply(null,b.subarray(0,300)).toLowerCase().includes('bot manager'));
    return {len:b.length,head:String.fromCharCode.apply(null,b.subarray(0,5)),bot:bot,b64:btoa(s)};
  }catch(e){return{len:0,head:'',bot:false,b64:''}}}"""

async def main():
    todo = [g for g in GAPS if not valid_pdf(fpath(g["doi"], g["vol"], g["iss"]))]
    print(f"IOP gaps: {len(GAPS)} total, {len(GAPS)-len(todo)} already, {len(todo)} to fetch")
    if not todo:
        print("All gaps already downloaded."); return
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP); ctx = br.contexts[0]
        page = await ctx.new_page()
        await page.goto("https://iopscience.iop.org/journal/0953-8984", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1)
        ok = fail = 0
        for g in todo:
            doi, vol, iss = g["doi"], g["vol"], g["iss"]
            fp = fpath(doi, vol, iss); fp.parent.mkdir(parents=True, exist_ok=True)
            got = False
            for attempt in range(5):
                r = await page.evaluate(FETCH_JS, f"https://iopscience.iop.org/article/{doi}/pdf")
                if r.get("head") == "%PDF-" and r.get("len", 0) > 10000:
                    fp.write_bytes(base64.b64decode(r["b64"])); got = valid_pdf(fp)
                    if got: break
                cd = 45 if r.get("bot") else 6*(attempt+1)
                await asyncio.sleep(cd)
                try:
                    await page.goto("https://iopscience.iop.org/journal/0953-8984", timeout=40000)
                    await asyncio.sleep(1)
                except Exception: pass
            if got: ok += 1; print(f"  OK V33I{iss} {doi[-10:]} {fp.stat().st_size//1024}KB ({ok+fail}/{len(todo)})", flush=True)
            else: fail += 1; print(f"  FAIL V33I{iss} {doi[-10:]}", flush=True)
            await asyncio.sleep(DELAY)
        await page.close()
        print(f"\nGaps done: +{ok} ok, {fail} fail")

if __name__ == "__main__":
    asyncio.run(main())
