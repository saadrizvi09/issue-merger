"""
Scan ALL 2018 Science DOIs via Unpaywall to find legally-free PDF copies
(green OA in repositories: arXiv, PMC, university eprints, etc.)
Resume-safe. Produces science_2018_oa.json with downloadable PDF URLs.
"""
import requests, json, time
from pathlib import Path

PROJECT = Path("C:/Projects/Automate pdf merge journal")
ART = json.loads((PROJECT/"science_2018_articles.json").read_text(encoding='utf-8'))
OUT = PROJECT/"science_2018_oa.json"
EMAIL = "research@jsmith.dev"

# Flatten all DOIs
dois = []
for arts in ART["issues"].values():
    for a in arts: dois.append((a["doi"], a.get("volume"), a.get("issue"), a["title"][:120]))
for a in ART["no_issue"]:
    dois.append((a["doi"], None, None, a["title"][:120]))

# Resume
result = {}
if OUT.exists():
    result = json.loads(OUT.read_text(encoding='utf-8'))
print(f"Total DOIs: {len(dois)} | already scanned: {len(result)}")

sess = requests.Session()
oa_count = sum(1 for v in result.values() if v.get("pdf"))
scanned = len(result)
for i,(doi,vol,iss,title) in enumerate(dois):
    if doi in result: continue
    try:
        r = sess.get(f"https://api.unpaywall.org/v2/{doi}?email={EMAIL}", timeout=15)
        d = r.json()
        pdf=None; host=None; ver=None
        best = d.get("best_oa_location") or {}
        if best.get("url_for_pdf"):
            pdf=best["url_for_pdf"]; host=best.get("host_type"); ver=best.get("version")
        else:
            for l in d.get("oa_locations",[]):
                if l.get("url_for_pdf"):
                    pdf=l["url_for_pdf"]; host=l.get("host_type"); ver=l.get("version"); break
        result[doi] = {"vol":vol,"iss":iss,"oa":bool(d.get("is_oa")),"status":d.get("oa_status"),
                       "pdf":pdf,"host":host,"version":ver}
        if pdf: oa_count+=1
    except Exception as e:
        result[doi] = {"vol":vol,"iss":iss,"err":str(e)[:40]}
    scanned+=1
    if scanned % 100 == 0:
        OUT.write_text(json.dumps(result,indent=2), encoding='utf-8')
        print(f"  {scanned}/{len(dois)} scanned | {oa_count} with free PDF", flush=True)
    time.sleep(0.05)

OUT.write_text(json.dumps(result,indent=2), encoding='utf-8')

# Report
with_pdf = {k:v for k,v in result.items() if v.get("pdf")}
from collections import Counter
hosts = Counter(v.get("host") for v in with_pdf.values())
vers = Counter(v.get("version") for v in with_pdf.values())
print(f"\n=== UNPAYWALL SCAN COMPLETE ===")
print(f"Total DOIs: {len(dois)}")
print(f"With free PDF: {len(with_pdf)} ({len(with_pdf)/len(dois)*100:.1f}%)")
print(f"By host: {dict(hosts)}")
print(f"By version: {dict(vers)}")
