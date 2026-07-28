"""
Enumerate ALL 2018 Science (AAAS) articles from Crossref.
ISSN 0036-8075 (print) / 1095-9203 (online)
Groups by volume/issue for per-issue merging.
"""
import requests, json, time
from collections import defaultdict

ISSN = "0036-8075"
OUT = "science_2018_articles.json"
BASE = f"https://api.crossref.org/journals/{ISSN}/works"

all_items = []
cursor = "*"
batch = 0
while cursor:
    params = {
        "filter": "from-pub-date:2018-01-01,until-pub-date:2018-12-31,type:journal-article",
        "select": "DOI,title,volume,issue,page,published,published-print,type",
        "rows": 500,
        "cursor": cursor,
    }
    r = requests.get(BASE, params=params, timeout=60)
    d = r.json()["message"]
    items = d.get("items", [])
    if not items:
        break
    all_items.extend(items)
    batch += 1
    cursor = d.get("next-cursor")
    print(f"Batch {batch}: +{len(items)} (total {len(all_items)})", flush=True)
    time.sleep(0.3)

print(f"\nTotal 2018 articles: {len(all_items)}")

# Group by volume/issue
issues = defaultdict(list)
no_issue = []
for it in all_items:
    doi = it.get("DOI", "")
    vol = it.get("volume")
    iss = it.get("issue")
    title = (it.get("title", ["?"])[0]) if it.get("title") else "?"
    # get issue publication date year
    page = it.get("page", "")
    rec = {"doi": doi, "title": title[:250], "volume": vol, "issue": iss, "page": page}
    if vol and iss:
        issues[f"V{vol}I{iss}"].append(rec)
    else:
        no_issue.append(rec)

print(f"Distinct issues (vol+issue): {len(issues)}")
print(f"Articles with no vol/issue: {len(no_issue)}")

# Volume distribution
vols = defaultdict(int)
for k, arts in issues.items():
    v = k[1:].split("I")[0]
    vols[v] += len(arts)
print("\nBy volume:")
for v in sorted(vols):
    print(f"  Vol {v}: {vols[v]} articles")

# Per-issue counts
print(f"\nPer-issue counts ({len(issues)} issues):")
for k in sorted(issues.keys(), key=lambda x:(int(x[1:].split('I')[0]), int(x.split('I')[1]))):
    print(f"  {k}: {len(issues[k])}")

out = {
    "journal": "Science",
    "issn": ISSN,
    "year": 2018,
    "total_articles": len(all_items),
    "articles_with_issue": sum(len(v) for v in issues.values()),
    "articles_without_issue": len(no_issue),
    "issues": {k: issues[k] for k in sorted(issues.keys())},
    "no_issue": no_issue,
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\nSaved to {OUT}")
