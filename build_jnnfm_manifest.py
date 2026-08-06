"""Build DOI manifest for J. Non-Newtonian Fluid Mechanics (ISSN 0377-0257), 2018-2026, via Crossref."""
import json, urllib.request, urllib.parse, time
from pathlib import Path

ISSN = "0377-0257"
OUT = Path("C:/Projects/Automate pdf merge journal/jnnfm_0377-0257_dois.json")
MIN_YEAR, MAX_YEAR = 2018, 2026

def fetch_all():
    base = f"https://api.crossref.org/journals/{ISSN}/works"
    cursor = "*"
    items = []
    while True:
        q = urllib.parse.urlencode({
            "filter": f"from-pub-date:{MIN_YEAR}-01-01,until-pub-date:{MAX_YEAR}-12-31,type:journal-article",
            "rows": 1000,
            "cursor": cursor,
            "select": "DOI,title,volume,issue,page,published-print,published-online,alternative-id,container-title",
        })
        url = f"{base}?{q}"
        req = urllib.request.Request(url, headers={"User-Agent": "manifest-builder/1.0 (mailto:ettools.three@timesinternet.in)"})
        data = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
        msg = data["message"]
        batch = msg["items"]
        if not batch:
            break
        items.extend(batch)
        cursor = msg.get("next-cursor")
        print(f"  fetched {len(items)} ...", flush=True)
        if not cursor or len(batch) < 1000:
            break
        time.sleep(0.5)
    return items

def year_of(it):
    for key in ("published-print", "published-online"):
        dp = it.get(key, {}).get("date-parts", [[None]])
        if dp and dp[0] and dp[0][0]:
            return dp[0][0]
    return 0

def pii_of(it):
    # Elsevier PII often present in alternative-id as S....
    for aid in it.get("alternative-id", []):
        a = aid.replace("-", "").replace("(", "").replace(")", "")
        if a.startswith("S") and len(a) >= 15:
            return a
    return None

items = fetch_all()
issues = {}
n = 0
for it in items:
    y = year_of(it)
    if not (MIN_YEAR <= y <= MAX_YEAR):
        continue
    vol = it.get("volume", "?"); iss = it.get("issue", "?")
    rec = {
        "doi": it["DOI"],
        "year": y,
        "volume": vol,
        "issue": iss,
        "page": it.get("page", ""),
        "pii": pii_of(it),
        "title": (it.get("title") or [""])[0][:200],
    }
    issues.setdefault(f"{vol}-{iss}", []).append(rec)
    n += 1

out = {"journal": "Journal of Non-Newtonian Fluid Mechanics", "issn": ISSN, "issues": issues}
OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

# Summary
from collections import Counter
byyear = Counter(a["year"] for arts in issues.values() for a in arts)
withpii = sum(1 for arts in issues.values() for a in arts if a["pii"])
print(f"\nTotal articles 2018-2026: {n}")
print(f"With PII: {withpii}/{n}")
print("By year:", dict(sorted(byyear.items())))
print(f"Issues (vol-iss groups): {len(issues)}")
print(f"Saved: {OUT}")
