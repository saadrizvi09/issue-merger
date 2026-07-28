"""Enumerate the 4 Springer journals for 2018-2026 via Crossref cursor pagination.
Issue year = published-print (fallback issued / published-online). Groups by V<vol>I<iss>.
Writes springer_dois.json = {journals:{jid:{name,issn,total,issues:{...}}}}."""
import urllib.request, urllib.parse, json, time
from pathlib import Path
from collections import defaultdict

PROJECT = Path("C:/Projects/Automate pdf merge journal")
OUT = PROJECT/"springer_dois.json"
JOURNALS = [
    ("180",   "Computational Statistics",          "0943-4062"),
    ("245",   "Applied Mathematics and Optimization","0095-4616"),
    ("10626", "Discrete Event Dynamic Systems",     "0924-6703"),
    ("10851", "Journal of Mathematical Imaging and Vision", "0924-9907"),
]
SELECT = "DOI,title,volume,issue,page,published-print,published-online,issued"

def yof(part):
    try: return part["date-parts"][0][0]
    except Exception: return None
def issue_year(it):
    for k in ("published-print", "issued", "published-online"):
        y = yof(it.get(k, {}))
        if y: return y
    return None

def fetch_journal(issn):
    items = []; cursor = "*"
    base = f"https://api.crossref.org/journals/{issn}/works"
    while True:
        q = urllib.parse.urlencode({"filter": "from-pub-date:2010-01-01,type:journal-article",
                                    "rows": 1000, "cursor": cursor, "select": SELECT})
        url = f"{base}?{q}"
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "journal-archive/1.0 (mailto:joydip@bajarangs.com)"})
                r = json.load(urllib.request.urlopen(req, timeout=40)); break
            except Exception:
                if attempt == 3: raise
                time.sleep(2*(attempt+1))
        msg = r["message"]; batch = msg["items"]; items.extend(batch)
        cursor = msg.get("next-cursor")
        if not batch or not cursor: break
        time.sleep(0.4)
    return items

def main():
    out = {"journals": {}}
    for jid, name, issn in JOURNALS:
        print(f"Enumerating {jid} {name} ({issn}) ...", flush=True)
        items = fetch_journal(issn)
        issues = defaultdict(list); kept = 0; seen = set()
        for it in items:
            doi = it.get("DOI", "").lower()
            if not doi or doi in seen: continue
            y = issue_year(it)
            if y is None or not (2018 <= y <= 2026): continue
            seen.add(doi)
            vol = (it.get("volume") or "?").strip() or "?"
            iss = (it.get("issue") or "?").strip() or "?"
            issues[f"V{vol}I{iss}"].append({
                "doi": doi, "title": (it.get("title") or [""])[0][:200],
                "page": it.get("page", ""), "year": y, "volume": vol, "issue": iss,
                "online_year": yof(it.get("published-online", {})),
            })
            kept += 1
        yc = defaultdict(int)
        for arts in issues.values():
            for a in arts: yc[a["year"]] += 1
        out["journals"][jid] = {"name": name, "issn": issn, "total": kept, "issues": dict(issues)}
        print(f"  {kept} articles 2018-2026 | by year: {dict(sorted(yc.items()))}", flush=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    tot = sum(j["total"] for j in out["journals"].values())
    print(f"\nTOTAL across 4 Springer journals: {tot}. Wrote {OUT}")

if __name__ == "__main__":
    main()
