import json, os, re, time, base64
from playwright.sync_api import sync_playwright
safe=lambda d: re.sub(r'[^A-Za-z0-9._-]','_',d)
works=json.load(open("innm_manifest.json"))
have=set(f[:-4] for f in os.listdir("innm_pdf") if f.endswith(".pdf"))
miss=[w for w in works if safe(w["doi"]) not in have and (w.get("pdf_urls") or w.get("pmcid"))]
print("browser-fetch candidates:", len(miss), flush=True)
with sync_playwright() as p:
    br=p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx=br.contexts[0]; pg=ctx.new_page()
    pg.goto("https://www.tandfonline.com/journal/innm20", wait_until="domcontentloaded", timeout=60000)
    for _ in range(20):
        if "just a moment" not in pg.title().lower(): break
        time.sleep(2)
    print("TF title:", pg.title()[:45], flush=True)
    def fetch(url):
        return pg.evaluate("""async(url)=>{try{const r=await fetch(url,{credentials:'include'});if(!r.ok)return{ok:false,status:r.status};
            const b=new Uint8Array(await r.arrayBuffer());let s='';const C=0x8000;
            for(let i=0;i<b.length;i+=C)s+=String.fromCharCode.apply(null,b.subarray(i,i+C));return{ok:true,b64:btoa(s),len:b.length};}catch(e){return{ok:false};}}""",url)
    ok=0
    for i,w in enumerate(miss,1):
        out=f"innm_pdf/{safe(w['doi'])}.pdf"; got=False
        urls=[f"https://www.tandfonline.com/doi/pdf/{w['doi']}?download=true"]
        urls+=[u for u in (w.get("pdf_urls") or []) if u not in urls]
        if w.get("pmcid"): urls.append(f"https://europepmc.org/articles/PMC{w['pmcid']}?pdf=render")
        for u in urls:
            try:
                r=fetch(u)
                if r.get("ok") and r.get("len",0)>8000:
                    data=base64.b64decode(r["b64"])
                    if data[:5]==b"%PDF-": open(out,"wb").write(data); ok+=1; got=True; break
            except Exception: pass
        if i%20==0: print(f"  {i}/{len(miss)} ok={ok}", flush=True)
    print(f"BROWSER DONE ok={ok} | total innm on disk: {len([f for f in os.listdir('innm_pdf') if f.endswith('.pdf')])}", flush=True)
