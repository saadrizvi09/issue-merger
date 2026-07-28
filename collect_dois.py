"""
GSCS20 OA COLLECTOR: Collect ALL open-access articles from Crossref.
For genuine OA (CC-licensed) articles, we can download complete PDFs.
"""
import requests, json, time
from collections import defaultdict

ISSN = "0094-9655"
OUTPUT = "gscs20_oa_dois.json"

all_oa = []
cursor = "*"
batch = 0

print("Collecting ALL articles with license data from Crossref...")

while cursor:
    params = {
        "filter": "from-pub-date:2018-01-01,until-pub-date:2026-12-31,type:journal-article,has-license:true",
        "select": "DOI,title,volume,issue,page,published-print,license,link",
        "rows": 200,
        "cursor": cursor,
    }
    resp = requests.get(
        "https://api.crossref.org/journals/0094-9655/works",
        params=params, timeout=30
    )
    data = resp.json()
    items = data["message"].get("items", [])
    all_oa.extend(items)
    batch += 1
    print(f"  Batch {batch}: {len(items)} articles (total: {len(all_oa)})")
    cursor = data["message"].get("next-cursor")
    if not cursor:
        break
    time.sleep(0.3)

print(f"\nTotal with license data: {len(all_oa)}")

# Classify licenses
cc_articles = []
other_licensed = []

for item in all_oa:
    doi = item.get("DOI", "")
    title = (item.get("title", ["?"])[0])[:200] if item.get("title") else "?"
    vol = item.get("volume", "?")
    iss = item.get("issue", "?")
    page = item.get("page", "?")
    pub = item.get("published-print", {}).get("date-parts", [[0]])[0][0] if item.get("published-print") else "?"

    licenses = []
    is_cc = False
    for lic in item.get("license", []):
        lic_url = lic.get("URL", "")
        licenses.append(lic_url)
        if "creativecommons" in lic_url.lower():
            is_cc = True

    entry = {
        "doi": doi,
        "title": title,
        "volume": vol,
        "issue": iss,
        "page": page,
        "year": pub,
        "licenses": licenses,
        "is_cc": is_cc,
    }

    if is_cc:
        cc_articles.append(entry)
    else:
        other_licensed.append(entry)

print(f"CC-licensed (genuinely OA): {len(cc_articles)}")
print(f"Other license: {len(other_licensed)}")

# Group by issue
by_issue = defaultdict(list)
for art in cc_articles + other_licensed:
    by_issue[f"V{art['volume']}I{art['issue']}"].append(art)

print(f"Issues with OA articles: {len(by_issue)}")

# Save
output = {
    "issn": ISSN,
    "journal": "Journal of Statistical Computation and Simulation",
    "cc_articles": len(cc_articles),
    "other_licensed": len(other_licensed),
    "total_oa_candidates": len(all_oa),
    "articles": {
        "cc": cc_articles,
        "other": other_licensed,
    },
}
with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\nSaved to {OUTPUT}")

# Print CC articles
print(f"\nCC-licensed articles ({len(cc_articles)}):")
for art in cc_articles:
    print(f"  V{art['volume']}I{art['issue']} | {art['doi']} | {art['title'][:80]}")
