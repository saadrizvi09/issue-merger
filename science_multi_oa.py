"""
Multi-source OA resolver + per-year availability for Science 2018-2026.
Combines OpenAlex + Unpaywall + CORE (all legal OA aggregators) to find the
maximum free-PDF coverage. Reports true combined % per year, merged with the
already-measured Sci-Hub coverage.
Pure requests (reliable, no browser).
"""
import requests, time, random, json, sys
random.seed(11)
N = 40
YEARS = range(2018, 2027)
EMAIL = "research@jsmith.dev"
SCIHUB = {2018:75,2019:75,2020:75,2021:20,2022:40,2023:15,2024:0,2025:15,2026:10}

S = requests.Session()
S.headers.update({"User-Agent":"OAResolver/1.0 (mailto:%s)"%EMAIL})

def jget(url, params=None, tries=3, timeout=30):
    for _ in range(tries):
        try:
            r=S.get(url,params=params,timeout=timeout)
            if r.status_code==200: return r.json()
        except: time.sleep(1.5)
    return None

def via_unpaywall(doi):
    d=jget(f"https://api.unpaywall.org/v2/{doi}",{"email":EMAIL})
    if not d: return None
    b=d.get("best_oa_location") or {}
    if b.get("url_for_pdf"): return b["url_for_pdf"]
    for l in d.get("oa_locations",[]):
        if l.get("url_for_pdf"): return l["url_for_pdf"]
    return None

def via_openalex(doi):
    d=jget(f"https://api.openalex.org/works/doi:{doi}",
           {"select":"open_access,primary_location,best_oa_location,locations"})
    if not d: return None
    for key in ("best_oa_location","primary_location"):
        loc=d.get(key) or {}
        if loc.get("pdf_url"): return loc["pdf_url"]
    for loc in (d.get("locations") or []):
        if loc.get("is_oa") and loc.get("pdf_url"): return loc["pdf_url"]
    oa=d.get("open_access") or {}
    if oa.get("oa_url") and oa["oa_url"].lower().endswith(".pdf"): return oa["oa_url"]
    return None

def via_core(doi):
    # CORE public search (no key): discover endpoint
    d=jget("https://api.core.ac.uk/v3/search/works",
           {"q":f'doi:"{doi}"',"limit":1}, tries=2, timeout=25)
    if not d: return None
    for res in d.get("results",[]):
        if res.get("downloadUrl"): return res["downloadUrl"]
        for u in res.get("sourceFulltextUrls",[]) or []:
            if u.endswith(".pdf"): return u
    return None

def resolve(doi):
    """Return (source, pdf_url) or (None,None)."""
    for name,fn in (("openalex",via_openalex),("unpaywall",via_unpaywall),("core",via_core)):
        try:
            u=fn(doi)
            if u: return name,u
        except: pass
    return None,None

def sample(year,n):
    d=jget("https://api.crossref.org/journals/0036-8075/works",
           {"filter":f"from-pub-date:{year}-01-01,until-pub-date:{year}-12-31,type:journal-article",
            "rows":300,"select":"DOI"})
    out=[i["DOI"] for i in d["message"]["items"]] if d else []
    random.shuffle(out); return out[:n]

def main():
    print("Multi-source OA availability per year (OpenAlex+Unpaywall+CORE)\n")
    rows=[]
    srccount={"openalex":0,"unpaywall":0,"core":0}
    for y in YEARS:
        dois=sample(y,N)
        oa=0; tested=0
        for doi in dois:
            src,url=resolve(doi)
            tested+=1
            if url: oa+=1; srccount[src]+=1
            time.sleep(0.02)
        oa_pct=(oa/tested*100) if tested else 0
        sh=SCIHUB[y]
        union=min(100, sh+oa_pct - sh*oa_pct/100)
        rows.append((y,sh,oa_pct,union))
        print(f"  {y}: SciHub {sh:>3}% | multi-OA {oa_pct:4.0f}% (n={tested}) | COMBINED ~{union:.0f}%", flush=True)
    print("\n=== PER-YEAR % AVAILABLE (Science) ===")
    print(f"{'Year':<6}{'Sci-Hub':>9}{'OA(multi)':>11}{'COMBINED':>11}")
    for y,sh,oa,un in rows:
        print(f"{y:<6}{sh:>7}% {oa:>9.0f}% {un:>9.0f}%")
    print(f"\nOA sources that hit: {srccount}")
    json.dump([{"year":y,"scihub":sh,"multi_oa":round(oa),"combined":round(un)} for y,sh,oa,un in rows],
              open("science_year_avail.json","w"),indent=2)

if __name__=="__main__": main()
