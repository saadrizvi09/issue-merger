"""Download ONLY the 13 remaining APS rmp online-first articles (aps_rmp_gaps.json) via
JMI/ONOS IP + CDP Chrome on 9223. Gentle (conc 1, long delay) to avoid APS 429.
Reports per-DOI outcome (PDF / 404-not-posted / blocked). Saves to aps_rmp_downloads/V_I_/.
"""
import json, re, time, asyncio
from pathlib import Path
from playwright.async_api import async_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
GAPS = json.loads((PROJECT/"aps_rmp_gaps.json").read_text(encoding="utf-8"))
DL = PROJECT/"aps_rmp_downloads"/"V_I_"; DL.mkdir(parents=True, exist_ok=True)
CDP = "http://127.0.0.1:9223"
DELAY = 6.0

def safe(s): return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))
def valid_pdf(fp):
    try: return fp.exists() and fp.stat().st_size > 10000 and fp.read_bytes()[:5] == b"%PDF-"
    except Exception: return False

PROBE_JS = """async(u)=>{try{
    const r=await fetch(u,{credentials:'include',redirect:'follow'});
    const ct=(r.headers.get('content-type')||'').toLowerCase();
    try{ if(r.body&&r.body.cancel) r.body.cancel(); }catch(e){}
    return {status:r.status, pdf:ct.includes('pdf')};
  }catch(e){return{status:0,pdf:false}}}"""

BLOB_JS = """async(u)=>{
    const r=await fetch(u,{credentials:'include',redirect:'follow'});
    if(!r.ok) throw new Error('status '+r.status);
    const b=await r.blob();
    if(b.size<10000) throw new Error('too small '+b.size);
    const a=document.createElement('a');
    a.href=URL.createObjectURL(b); a.download='paper.pdf';
    document.body.appendChild(a); a.click();
    setTimeout(()=>{URL.revokeObjectURL(a.href); a.remove();}, 15000);
    return b.size;
}"""

async def wait_cf(page, m=30):
    for _ in range(m):
        try:
            t = (await page.title()).lower()
            if "just a moment" not in t and "loading" not in t and t.strip(): return True
        except Exception: pass
        await asyncio.sleep(1)
    return False

async def main():
    todo = [d for d in GAPS if not valid_pdf(DL/f"{safe(d)}.pdf")]
    print(f"rmp gaps: {len(GAPS)} total, {len(todo)} to fetch")
    async with async_playwright() as p:
        br = await p.chromium.connect_over_cdp(CDP); ctx = br.contexts[0]
        page = await ctx.new_page()
        await page.goto("https://journals.aps.org/rmp/", wait_until="domcontentloaded", timeout=60000)
        await wait_cf(page); await asyncio.sleep(2)
        ip = await page.evaluate("async()=>{try{return await (await fetch('https://api.ipify.org')).text()}catch(e){return '?'}}")
        print("egress IP:", ip)
        ok = notposted = blocked = 0
        for doi in todo:
            url = f"https://journals.aps.org/rmp/pdf/{doi}"
            fp = DL/f"{safe(doi)}.pdf"
            # probe status first (lightweight, no body)
            try:
                pr = await asyncio.wait_for(page.evaluate(PROBE_JS, url), timeout=40)
            except Exception:
                pr = {"status": 0, "pdf": False}
            st = pr.get("status")
            if st == 404:
                print(f"  {doi[-9:]} -> 404 NOT POSTED (online-first PDF not available yet)", flush=True); notposted += 1
                await asyncio.sleep(DELAY); continue
            if st in (429, 403, 401):
                print(f"  {doi[-9:]} -> {st} BLOCKED/rate-limited — backing off 30s", flush=True); blocked += 1
                await asyncio.sleep(30); continue
            # looks fetchable -> blob download
            got = False
            for attempt in range(3):
                try:
                    async with page.expect_download(timeout=150000) as di:
                        await asyncio.wait_for(page.evaluate(BLOB_JS, url), timeout=150)
                    dl = await di.value; await dl.save_as(str(fp))
                    if valid_pdf(fp): got = True; break
                except Exception:
                    await asyncio.sleep(5*(attempt+1))
            if got: print(f"  {doi[-9:]} -> OK %PDF- {fp.stat().st_size//1024}KB", flush=True); ok += 1
            else: print(f"  {doi[-9:]} -> FAIL (status was {st})", flush=True); blocked += 1
            await asyncio.sleep(DELAY)
        await page.close()
        print(f"\nGaps: {ok} downloaded, {notposted} not-posted(404), {blocked} blocked/failed")

if __name__ == "__main__":
    asyncio.run(main())
