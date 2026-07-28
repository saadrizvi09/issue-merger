"""Enumerate the 2 T&F journals (lagb20, umma20) for 2018-2026 via Crossref.
Issue year = published-print (fallback issued/online). Groups by V<vol>I<iss>.
Writes tandf_dois.json = {journals:{code:{name,issn,total,issues:{...}}}}."""
import urllib.request, urllib.parse, json, time
from pathlib import Path
from collections import defaultdict

PROJECT = Path("C:/Projects/Automate pdf merge journal")
OUT = PROJECT/"tandf_dois.json"
JOURNALS = [
    ("lagb20", "Communications in Algebra", "0092-7872"),
    ("umma20", "American Journal of Mathematical and Management Sciences", "0746-8342"),
]
SELECT = "DOI,title,container-title,volume,issue,page,published-print,published-online,issued"

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
        for attempt in range(4):
            try:
                req = urllib.request.Request(f"{base}?{q}", headers={"User-Agent": "journal-archive/1.0 (mailto:joydip@bajarangs.com)"})
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
    for code, name, issn in JOURNALS:
        print(f"Enumerating {code} {name} ({issn}) ...", flush=True)
        items = fetch_journal(issn)
        issues = defaultdict(list); kept = 0; seen = set(); ctitle = name
        for it in items:
            doi = it.get("DOI", "").lower()
            if not doi or doi in seen: continue
            y = issue_year(it)
            if y is None or not (2018 <= y <= 2026): continue
            seen.add(doi)
            ct = it.get("container-title") or []
            if ct: ctitle = ct[0]
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
        out["journals"][code] = {"name": ctitle, "issn": issn, "total": kept, "issues": dict(issues)}
        print(f"  {ctitle}: {kept} articles | by year: {dict(sorted(yc.items()))}", flush=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    tot = sum(j["total"] for j in out["journals"].values())
    print(f"\nTOTAL across T&F journals: {tot}. Wrote {OUT}")

if __name__ == "__main__":
    main()
