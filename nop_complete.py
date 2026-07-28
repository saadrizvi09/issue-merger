import json, os, re, time, base64
from playwright.sync_api import sync_playwright
safe=lambda d: re.sub(r'[^A-Za-z0-9._-]','_',d)
miss=json.load(open("nop_miss2.json"))
have=set(f[:-4] for f in os.listdir("nop_pdf") if f.endswith(".pdf"))
miss=[r for r in miss if safe(r["doi"]) not in have]
print("nop to complete:", len(miss), flush=True)
with sync_playwright() as p:
    br=p.chromium.connect_over_cdp("http://127.0.0.1:9222"); ctx=br.contexts[0]; pg=ctx.new_page()
    def fetchpdf(url):
        return pg.evaluate("""async(url)=>{try{const r=await fetch(url,{credentials:'include'});if(!r.ok)return{ok:false,st:r.status};
            const ct=r.headers.get('content-type')||'';const b=new Uint8Array(await r.arrayBuffer());
            let s='';const C=0x8000;for(let i=0;i<b.length;i+=C)s+=String.fromCharCode.apply(null,b.subarray(i,i+C));
            return{ok:true,b64:btoa(s),len:b.length,ct};}catch(e){return{ok:false};}}""",url)
    ok=0; fails=[]
    for i,r in enumerate(miss,1):
        out=f"nop_pdf/{safe(r['doi'])}.pdf"; got=False
        try:
            pg.goto(f"https://doi.org/{r['doi']}", wait_until="domcontentloaded", timeout=45000)
            for _ in range(8):
                if "just a moment" not in pg.title().lower(): break
                time.sleep(2)
            html=pg.content()
            pdfurls=re.findall(r'citation_pdf_url"\s+content="([^"]+)"', html)
            pdfurls+=re.findall(r'href="(/nop/article-pdf/[^"]+\.pdf[^"]*)"', html)
            seen=set(); cand=[]
            for u in pdfurls:
                if u.startswith("/"): u="https://academic.oup.com"+u
                if u not in seen: seen.add(u); cand.append(u)
            for u in cand:
                res=fetchpdf(u)
                if res.get("ok") and res.get("len",0)>8000:
                    d=base64.b64decode(res["b64"])
                    if d[:5]==b"%PDF-": open(out,"wb").write(d); ok+=1; got=True; break
        except Exception: pass
        if not got: fails.append(f"V{r['vol']}I{r['issue']} {r['doi']}")
        if i%10==0: print(f"  {i}/{len(miss)} ok={ok}", flush=True)
    print(f"NOP COMPLETE ok={ok}/{len(miss)}", flush=True)
    print("still failing:", fails[:60], flush=True)
