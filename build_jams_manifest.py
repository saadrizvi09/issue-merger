"""Build JAMS (ISSN 0894-0347) manifest for the FREE archive print-years 2018-2020 (vols 31-33)
from Crossref. Parses the AMS link URL for print year/vol/issue + the S-number, and constructs
the final published-PDF URL. Free archive = print 1988-2020, so vols 31-33 are free."""
import json, urllib.request, urllib.parse, re, time
from pathlib import Path

ISSN = "0894-0347"
OUT = Path("C:/Projects/Automate pdf merge journal/jams_0894-0347_dois.json")
FREE_VOLS = {31, 32, 33}   # print 2018, 2019, 2020

def fetch_all():
    base = f"https://api.crossref.org/journals/{ISSN}/works"
    cursor = "*"; items = []
    while True:
        q = urllib.parse.urlencode({
            "filter": "from-pub-date:2016-01-01,until-pub-date:2022-12-31,type:journal-article",
            "rows": 500, "cursor": cursor,
            "select": "DOI,title,volume,issue,page,link,published-print,published-online",
        })
        req = urllib.request.Request(f"{base}?{q}", headers={"User-Agent": "manifest/1.0 (mailto:ettools.three@timesinternet.in)"})
        data = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
        batch = data["message"]["items"]
        if not batch: break
        items.extend(batch)
        cursor = data["message"].get("next-cursor")
        if not cursor or len(batch) < 500: break
        time.sleep(0.4)
    return items

# link URL pattern: .../jams/2020-33-01/S0894-0347-2019-00932-X/jams932_AM.pdf
LINKRE = re.compile(r'/jams/(\d{4})-(\d+)-(\d+)/(S0894-0347-\d{4}-\d+-[A-Z0-9])/')

items = fetch_all()
issues = {}
n = 0
skipped_nolink = 0
for it in items:
    links = [l.get("URL", "") for l in it.get("link", [])]
    m = None
    for u in links:
        m = LINKRE.search(u)
        if m: break
    if not m:
        skipped_nolink += 1
        continue
    pyear, vol, iss, snum = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
    if vol not in FREE_VOLS:
        continue
    # final published PDF (confirmed downloadable, no login)
    pdf_url = f"https://www.ams.org/journals/jams/{pyear}-{vol:02d}-{iss}/{snum}/{snum}.pdf"
    rec = {
        "doi": it["DOI"], "year": pyear, "volume": str(vol), "issue": str(int(iss)),
        "snum": snum, "pdf_url": pdf_url,
        "title": (it.get("title") or [""])[0][:200],
    }
    issues.setdefault(f"{vol}-{int(iss)}", []).append(rec)
    n += 1

out = {"journal": "Journal of the American Mathematical Society", "issn": ISSN, "issues": issues}
OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

from collections import Counter
byyear = Counter(a["year"] for arts in issues.values() for a in arts)
print(f"Fetched {len(items)} works; {skipped_nolink} had no AMS link.")
print(f"FREE JAMS (print vols 31-33 = 2018-2020): {n} articles")
print("By print year:", dict(sorted(byyear.items())))
print(f"Issues: {len(issues)}")
print(f"Saved: {OUT}")
