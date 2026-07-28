import json, os, re, time, base64
from playwright.sync_api import sync_playwright
safe=lambda d: re.sub(r'[^A-Za-z0-9._-]','_',d)
miss=json.load(open("nop_miss2.json"))
have=set(f[:-4] for f in os.listdir("nop_pdf") if f.endswith(".pdf"))
miss=[r for r in miss if safe(r["doi"]) not in have]
print("nop remaining:", len(miss), flush=True)
with sync_playwright() as p:
    br=p.chromium.connect_over_cdp("http://127.0.0.1:9222"); ctx=br.contexts[0]; pg=ctx.new_page()
    pg.goto("https://academic.oup.com/nop", wait_until="domcontentloaded", timeout=60000)
    for _ in range(12):
        if "just a moment" not in pg.title().lower(): break
        time.sleep(2)
    def fetch(url):
        return pg.evaluate("""async(url)=>{try{const r=await fetch(url,{credentials:'include'});if(!r.ok)return{ok:false};
            const b=new Uint8Array(await r.arrayBuffer());let s='';const C=0x8000;for(let i=0;i<b.length;i+=C)s+=String.fromCharCode.apply(null,b.subarray(i,i+C));return{ok:true,b64:btoa(s),len:b.length};}catch(e){return{ok:false};}}""",url)
    ok=0
    for r in miss:
        urls=[f"https://academic.oup.com/nop/article-pdf/doi/{r['doi']}/nop.pdf"]
        if r.get("pmcid"): urls=[f"https://europepmc.org/articles/PMC{r['pmcid']}?pdf=render",f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{r['pmcid']}/pdf/"]+urls
        for u in urls:
            try:
                res=fetch(u)
                if res.get("ok") and res.get("len",0)>8000:
                    d=base64.b64decode(res["b64"])
                    if d[:5]==b"%PDF-": open(f"nop_pdf/{safe(r['doi'])}.pdf","wb").write(d); ok+=1; break
            except Exception: pass
    print(f"nop browser recovered {ok}/{len(miss)}", flush=True)
