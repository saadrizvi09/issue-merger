"""
Per-year AVAILABILITY for Science 2018-2026 = green-OA (Unpaywall) measured now,
combined with previously-measured Sci-Hub coverage.
Samples DOIs/year via Crossref, checks Unpaywall for a free PDF. Pure requests (reliable).
"""
import requests, time, random, json
random.seed(7)
N=40
YEARS=range(2018,2027)
EMAIL="research@jsmith.dev"

# Sci-Hub coverage measured earlier (20-DOI samples)
SCIHUB={2018:75,2019:75,2020:75,2021:20,2022:40,2023:15,2024:0,2025:15,2026:10}

def get_json(url, params=None, tries=4):
    for _ in range(tries):
        try: return requests.get(url,params=params,timeout=40).json()
        except: time.sleep(2)
    return None

def sample(year,n):
    out=[]
    d=get_json('https://api.crossref.org/journals/0036-8075/works',
        {'filter':f'from-pub-date:{year}-01-01,until-pub-date:{year}-12-31,type:journal-article',
         'rows':300,'select':'DOI'})
    if d: out=[i['DOI'] for i in d['message']['items']]
    random.shuffle(out); return out[:n]

def green_oa(doi):
    d=get_json(f'https://api.unpaywall.org/v2/{doi}?email={EMAIL}',tries=3)
    if not d: return None
    best=d.get('best_oa_location') or {}
    if best.get('url_for_pdf'): return True
    for l in d.get('oa_locations',[]):
        if l.get('url_for_pdf'): return True
    return False

print("Measuring green-OA availability per year (Unpaywall)...\n")
rows=[]
for y in YEARS:
    dois=sample(y,N)
    oa=0; tested=0
    for doi in dois:
        r=green_oa(doi)
        if r is None: continue
        tested+=1
        if r: oa+=1
        time.sleep(0.03)
    oa_pct=(oa/tested*100) if tested else 0
    sh=SCIHUB[y]
    # Combined (union) estimate: assume partial overlap; union >= max, <= min(100, sh+oa)
    # Use inclusion-exclusion with independence approx: union = sh+oa - sh*oa/100
    union=min(100, sh+oa_pct - sh*oa_pct/100)
    rows.append((y,sh,oa_pct,union))
    print(f"  {y}: SciHub {sh:>3}% | green-OA {oa_pct:4.0f}% (n={tested}) | COMBINED ~{union:.0f}%", flush=True)

print("\n=== PER-YEAR % AVAILABLE (Science, free routes) ===")
print(f"{'Year':6}{'Sci-Hub':>9}{'GreenOA':>9}{'Combined':>10}")
for y,sh,oa,un in rows:
    print(f"{y:<6}{sh:>7}% {oa:>7.0f}% {un:>8.0f}%")
json.dump([{'year':y,'scihub':sh,'green_oa':round(oa),'combined':round(un)} for y,sh,oa,un in rows],
          open('science_year_avail.json','w'),indent=2)
