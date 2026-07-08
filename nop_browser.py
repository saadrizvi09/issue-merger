import json, os, re, time, base64
from playwright.sync_api import sync_playwright
safe=lambda d: re.sub(r'[^A-Za-z0-9._-]','_',d)
works=json.load(open("nop_manifest.json"))
have=set(f[:-4] for f in os.listdir("nop_pdf") if f.endswith(".pdf"))
miss=[w for w in works if safe(w["doi"]) not in have and (w.get("pmcid") or w.get("pdf_urls"))]
print("browser candidates:", len(miss), flush=True)
with sync_playwright() as p:
    br=p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx=br.contexts[0]; pg=ctx.new_page()
    pg.goto("https://academic.oup.com/nop", wait_until="domcontentloaded", timeout=60000)
    for _ in range(12):
        if "just a moment" not in pg.title().lower(): break
        time.sleep(2)
    print("OUP ready:", pg.title()[:40], flush=True)
    def fetch(url):
        return pg.evaluate("""async(url)=>{try{const r=await fetch(url,{credentials:'include'});if(!r.ok)return{ok:false};
            const b=new Uint8Array(await r.arrayBuffer());let s='';const C=0x8000;
            for(let i=0;i<b.length;i+=C)s+=String.fromCharCode.apply(null,b.subarray(i,i+C));return{ok:true,b64:btoa(s),len:b.length};}catch(e){return{ok:false};}}""",url)
    ok=0
    for i,w in enumerate(miss,1):
        out=f"nop_pdf/{safe(w['doi'])}.pdf"
        urls=list(w.get("pdf_urls") or [])
        if w.get("pmcid"): urls+= [f"https://europepmc.org/articles/PMC{w['pmcid']}?pdf=render",
                                   f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{w['pmcid']}/pdf/"]
        for u in urls:
            try:
                r=fetch(u)
                if r.get("ok") and r.get("len",0)>8000:
                    data=base64.b64decode(r["b64"])
                    if data[:5]==b"%PDF-": open(out,"wb").write(data); ok+=1; break
            except Exception: pass
        if i%15==0: print(f"  {i}/{len(miss)} ok={ok}", flush=True)
    print(f"BROWSER DONE ok={ok} | nop on disk: {len([f for f in os.listdir('nop_pdf') if f.endswith('.pdf')])}", flush=True)
