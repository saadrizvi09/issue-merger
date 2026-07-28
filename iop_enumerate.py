"""Enumerate all IOP J.Phys: Condensed Matter (ISSN 0953-8984) articles for 2018-2026
via Crossref cursor pagination. Issue year = published-print (fallback issued /
published-online). Groups by V<vol>I<issue>. Writes iop_0953-8984_dois.json."""
import urllib.request, urllib.parse, json, time, re
from pathlib import Path
from collections import defaultdict

PROJECT = Path("C:/Projects/Automate pdf merge journal")
ISSN = "0953-8984"
OUT = PROJECT/"iop_0953-8984_dois.json"
SELECT = "DOI,title,volume,issue,page,published-print,published-online,issued"

def year_of(part):
    try: return part["date-parts"][0][0]
    except Exception: return None

def issue_year(it):
    for k in ("published-print", "issued", "published-online"):
        y = year_of(it.get(k, {}))
        if y: return y, ("print" if k=="published-print" else k)
    return None, None

def fetch_all():
    items = []
    cursor = "*"
    base = f"https://api.crossref.org/journals/{ISSN}/works"
    n_req = 0
    while True:
        q = urllib.parse.urlencode({
            "filter": "from-pub-date:2017-06-01",   # wide; issue-year filtered in code
            "rows": 1000, "cursor": cursor, "select": SELECT,
        })
        url = f"{base}?{q}"
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "journal-archive/1.0 (mailto:joydip@bajarangs.com)"})
                r = json.load(urllib.request.urlopen(req, timeout=40))
                break
            except Exception as e:
                if attempt == 3:
                    raise
                time.sleep(2*(attempt+1))
        msg = r["message"]
        batch = msg["items"]
        items.extend(batch)
        n_req += 1
        cursor = msg.get("next-cursor")
        print(f"  req {n_req}: +{len(batch)} (total {len(items)})", flush=True)
        if not batch or not cursor:
            break
        time.sleep(0.5)
    return items

def main():
    print("Enumerating Crossref for ISSN", ISSN, "...")
    items = fetch_all()
    issues = defaultdict(list)
    kept = 0; noiss = 0
    seen = set()
    for it in items:
        doi = it.get("DOI", "").lower()
        if not doi or doi in seen: continue
        y, src = issue_year(it)
        if y is None or not (2018 <= y <= 2026): continue
        seen.add(doi)
        vol = (it.get("volume") or "?").strip() or "?"
        iss = (it.get("issue") or "?").strip() or "?"
        oy = year_of(it.get("published-online", {}))
        rec = {
            "doi": doi,
            "title": (it.get("title") or [""])[0][:200],
            "page": it.get("page", ""),
            "year": y, "year_src": src,
            "volume": vol, "issue": iss,
            "online_year": oy,
        }
        issues[f"V{vol}I{iss}"].append(rec)
        kept += 1
        if vol == "?" or iss == "?": noiss += 1
    data = {"issn": ISSN, "journal": "Journal of Physics: Condensed Matter",
            "total_articles": kept, "issues": dict(issues)}
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    # report
    yc = defaultdict(int)
    for arts in issues.values():
        for a in arts: yc[a["year"]] += 1
    print(f"\nTotal 2018-2026 articles: {kept}  (no vol/issue: {noiss})")
    print("By issue-year:")
    for y in range(2018, 2027):
        print(f"  {y}: {yc.get(y,0)}")
    print("Wrote", OUT)

if __name__ == "__main__":
    main()
