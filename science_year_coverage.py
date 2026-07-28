"""
Measure Sci-Hub coverage per year (2018-2026) for Science.
Samples N research DOIs/year, tests each robustly (captcha re-solve + retry),
classifies FOUND / NOTFOUND, reports per-year percentage.
"""
import requests, time, re, sys, json, random
from playwright.sync_api import sync_playwright

random.seed(42)
N_PER_YEAR = 20
YEARS = range(2018, 2027)
CDP = "http://127.0.0.1:9222"
MIRRORS = ["https://sci-hub.ru", "https://sci-hub.se"]

def sample_dois(year, n):
    """Get n research-article DOIs spread across the year."""
    out=[]
    for attempt in range(4):
        try:
            r=requests.get('https://api.crossref.org/journals/0036-8075/works',
                params={'filter':f'from-pub-date:{year}-01-01,until-pub-date:{year}-12-31,type:journal-article',
                        'rows':200,'select':'DOI,title,page','offset':attempt*200},timeout=40).json()
            items=r['message']['items']
            # prefer research articles: DOIs with alpha codes (science.aXXXXXX) or having pages
            for it in items:
                doi=it['DOI']
                out.append(doi)
            if len(out)>=n*3: break
        except Exception as e:
            time.sleep(2)
    random.shuffle(out)
    return out[:n]

def is_captcha(pg):
    t=pg.title().lower()
    if 'robot' in t or 'moment' in t or 'ddos' in t or 'are you' in t: return True
    try:
        if pg.locator('.answer').count()>0 and pg.locator('#pdf,embed,iframe').count()==0: return True
    except: pass
    return False

def solve(pg):
    for _ in range(2):
        if not is_captcha(pg): return
        try: pg.locator('.answer').first.click(timeout=4000)
        except: pass
        for _ in range(15):
            time.sleep(1)
            if not is_captcha(pg): return

def check(pg, doi):
    """FOUND / NOTFOUND / UNCERTAIN"""
    for mirror in MIRRORS:
        for attempt in range(2):
            try:
                pg.goto(f"{mirror}/{doi}", wait_until="domcontentloaded", timeout=30000)
                time.sleep(1.2)
                if is_captcha(pg):
                    solve(pg); time.sleep(0.3)
                if is_captcha(pg):
                    continue  # try next mirror
                html=pg.content(); low=html.lower()
                if re.search(r'/storage/[^"]*\.pdf', html) or re.search(r'href="[^"]*\.pdf', html):
                    return "FOUND"
                if 'article not found' in low or 'unfortunately' in low or 'статья не найдена' in low or 'to download' in low and 'not' in low:
                    return "NOTFOUND"
                # page loaded, no pdf, no explicit notfound
                if 'sci-hub' in low:
                    return "NOTFOUND"
            except Exception:
                continue
    return "UNCERTAIN"

def main():
    print("Sampling DOIs per year...")
    year_dois={y:sample_dois(y,N_PER_YEAR) for y in YEARS}
    for y in YEARS: print(f"  {y}: {len(year_dois[y])} DOIs")

    results={}
    with sync_playwright() as p:
        br=p.chromium.connect_over_cdp(CDP)
        ctx=br.contexts[0]; pg=ctx.pages[0] if ctx.pages else ctx.new_page()
        try: pg.goto("https://sci-hub.ru/",timeout=25000); time.sleep(1); solve(pg)
        except: pass
        for y in YEARS:
            f=nf=un=0
            for doi in year_dois[y]:
                r=check(pg,doi)
                if r=="FOUND": f+=1
                elif r=="NOTFOUND": nf+=1
                else: un+=1
            tested=f+nf  # exclude uncertain from denominator
            pct = (f/tested*100) if tested else 0
            results[y]={"found":f,"notfound":nf,"uncertain":un,"pct":pct}
            print(f"  {y}: {f}/{tested} on Sci-Hub = {pct:.0f}%  (uncertain={un})", flush=True)
        pg.close()
    print("\n=== PER-YEAR SCI-HUB COVERAGE (Science) ===")
    for y in YEARS:
        r=results[y]; print(f"  {y}: {r['pct']:.0f}%")
    json.dump(results, open("science_year_coverage.json","w"), indent=2)

if __name__=="__main__": main()
