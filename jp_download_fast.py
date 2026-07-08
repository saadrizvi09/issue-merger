#!/usr/bin/env python3
"""FAST journal PDF downloader. Primary source = PMC Open Access S3 bucket
(static, no rate limit, ~4x faster than the render endpoint, tolerates high
concurrency). Fallbacks: publisher pdf_urls, then europepmc render.
Usage: python jp_download_fast.py <manifest.json> <outdir> [workers]
Manifest = list of {doi, pmcid, pdf_urls}. Resume-safe."""
import json, os, re, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
MF=sys.argv[1]; OUT=sys.argv[2]; WORKERS=int(sys.argv[3]) if len(sys.argv)>3 else 48
os.makedirs(OUT, exist_ok=True)
MAIL="clasherizvi@gmail.com"
safe=lambda d: re.sub(r'[^A-Za-z0-9._-]','_',d)
S3="https://pmc-oa-opendata.s3.amazonaws.com"
def raw(u,t=60):
    return urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0 mailto:"+MAIL,"Accept":"application/pdf,*/*"}),timeout=t)
def getpdf(u,t=60):
    try:
        d=raw(u,t).read()
        return d if (d[:5]==b"%PDF-" and len(d)>8000) else None
    except Exception: return None
def s3_pdf(pmc):
    # try version .1 directly (most common), else list to find real version
    d=getpdf(f"{S3}/PMC{pmc}.1/PMC{pmc}.1.pdf")
    if d: return d
    try:
        xml=raw(f"{S3}/?list-type=2&prefix=PMC{pmc}.",30).read().decode("utf-8","replace")
        keys=re.findall(r"<Key>([^<]+\.pdf)</Key>", xml)
        if keys: return getpdf(f"{S3}/{urllib.parse.quote(keys[0])}")
    except Exception: pass
    return None
def dl(w):
    p=f"{OUT}/{safe(w['doi'])}.pdf"
    pid=w.get("pmcid") or w.get("pmcid2")
    if pid:
        d=s3_pdf(pid)
        if d: open(p,"wb").write(d); return "s3"
    for u in (w.get("pdf_urls") or []):
        if "ncbi" in u: continue
        d=getpdf(u)
        if d: open(p,"wb").write(d); return "pub"
    if pid:
        d=getpdf(f"https://europepmc.org/articles/PMC{pid}?pdf=render",70)
        if d: open(p,"wb").write(d); return "render"
    return "fail"
works=json.load(open(MF))
jobs=[w for w in works if (w.get("pmcid") or w.get("pmcid2") or w.get("pdf_urls"))
      and not (os.path.exists(f"{OUT}/{safe(w['doi'])}.pdf") and os.path.getsize(f"{OUT}/{safe(w['doi'])}.pdf")>8000)]
print(f"pending {len(jobs)} / {len(works)} | workers={WORKERS}", flush=True)
ok=0; via={}; t=time.time()
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    for n,f in enumerate(as_completed([ex.submit(dl,w) for w in jobs]),1):
        r=f.result()
        if r!="fail": ok+=1; via[r]=via.get(r,0)+1
        if n%100==0: print(f"  {n}/{len(jobs)} ok={ok} {via} {time.time()-t:.0f}s ({n/(time.time()-t):.1f}/s)", flush=True)
print(f"DONE ok={ok}/{len(jobs)} via={via} in {time.time()-t:.0f}s ({len(jobs)/max(time.time()-t,1):.1f}/s)", flush=True)
