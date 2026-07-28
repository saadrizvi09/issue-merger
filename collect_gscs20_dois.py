"""
Collect all DOIs from Crossref for Journal of Statistical Computation and Simulation
ISSN 0094-9655, years 2018-2026
"""
import requests
import json
import time
from collections import defaultdict

ISSN = "0094-9655"
OUTPUT = "gscs20_dois.json"

def fetch_all_dois():
    all_items = []
    base_url = "https://api.crossref.org/journals/0094-9655/works"
    params = {
        "filter": "from-pub-date:2018-01-01,until-pub-date:2026-12-31,type:journal-article",
        "select": "DOI,title,volume,issue,page,published-print",
        "rows": 1000,
    }

    cursor = "*"
    batch = 0
    while cursor:
        params["cursor"] = cursor
        resp = requests.get(base_url, params=params, timeout=60)
        data = resp.json()
        msg = data["message"]
        items = msg.get("items", [])
        all_items.extend(items)
        batch += 1
        print(f"Batch {batch}: {len(items)} articles (total: {len(all_items)})")
        cursor = msg.get("next-cursor")
        if cursor:
            time.sleep(0.5)  # Be polite

    # Group by volume/issue
    vi = defaultdict(list)
    for item in all_items:
        vol = item.get("volume", "?")
        iss = item.get("issue", "?")
        vi[f"V{vol}I{iss}"].append({
            "doi": item.get("DOI"),
            "title": (item.get("title", ["?"])[0])[:200] if item.get("title") else "?",
            "page": item.get("page", "?"),
        })

    # Print summary
    for k in sorted(vi.keys()):
        print(f"{k}: {len(vi[k])} articles")

    print(f"\nTotal: {len(all_items)} articles across {len(vi)} issues")

    # Save
    output = {
        "issn": ISSN,
        "journal": "Journal of Statistical Computation and Simulation",
        "total_articles": len(all_items),
        "issues": {k: vi[k] for k in sorted(vi.keys())},
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {OUTPUT}")
    return output

if __name__ == "__main__":
    fetch_all_dois()
