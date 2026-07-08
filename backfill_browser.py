import json, os, re, time, base64
from playwright.sync_api import sync_playwright
safe=lambda d: re.sub(r'[^A-Za-z0-9._-]','_',d)
JOBS=[("innm","innm_pdf","https://www.tandfonline.com/journal/innm20", lambda doi:[f"https://www.tandfonline.com/doi/pdf/{doi}?download=true"]),
      ("nml","nml_pdf","https://link.springer.com/journal/40820", lambda doi:[f"https://link.springer.com/content/pdf/{doi}.pdf"])]
with sync_playwright() as p:
    br=p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx=br.contexts[0]; pg=ctx.new_page()
    def fetch(url):
        return pg.evaluate("""async(url)=>{try{const r=await fetch(url,{credentials:'include'});if(!r.ok)return{ok:false};
            const b=new Uint8Array(await r.arrayBuffer());let s='';const C=0x8000;
            for(let i=0;i<b.length;i+=C)s+=String.fromCharCode.apply(null,b.subarray(i,i+C));return{ok:true,b64:btoa(s),len:b.length};}catch(e){return{ok:false};}}""",url)
    for k,pdfdir,warm,urlfn in JOBS:
        miss=json.load(open(f"{k}_crossref_miss.json"))
        have=set(f[:-4] for f in os.listdir(pdfdir) if f.endswith(".pdf"))
        miss=[r for r in miss if safe(r["doi"]) not in have]
        pg.goto(warm, wait_until="domcontentloaded", timeout=60000)
        for _ in range(15):
            if "just a moment" not in pg.title().lower(): break
            time.sleep(2)
        print(f"[{k}] warm: {pg.title()[:35]} | candidates {len(miss)}", flush=True)
        ok=0
        for i,r in enumerate(miss,1):
            out=f"{pdfdir}/{safe(r['doi'])}.pdf"
            for u in urlfn(r["doi"]):
                try:
                    res=fetch(u)
                    if res.get("ok") and res.get("len",0)>8000:
                        data=base64.b64decode(res["b64"])
                        if data[:5]==b"%PDF-": open(out,"wb").write(data); ok+=1; break
                except Exception: pass
            if i%100==0: print(f"[{k}]  {i}/{len(miss)} ok={ok}", flush=True)
        print(f"[{k}] BROWSER RECOVERED {ok}/{len(miss)}", flush=True)
    print("ALL BROWSER DONE", flush=True)
