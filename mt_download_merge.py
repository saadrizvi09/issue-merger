"""
Download all Model Theory individual article PDFs, merge per issue, clean.
MSP journal — all articles are free 2022–2026.
"""
import subprocess, os, sys, time, glob
from pypdf import PdfReader, PdfWriter

BASE = r"C:\Projects\Automate pdf merge journal"
ARTICLES_DIR = os.path.join(BASE, "mt_articles")
MERGED_DIR = os.path.join(BASE, "mt_merged")
CLEAN_DIR = os.path.join(BASE, "mt_clean")

# ISSN 2832-904X → 2832904X
ISSN = "2832904X"

# (year, vol, issue, article_count)
ISSUES = [
    ("2022", 1, 1, 5),
    ("2023", 2, 1, 4),
    ("2023", 2, 2, 13),
    ("2024", 3, 1, 6),
    ("2024", 3, 2, 22),
    ("2024", 3, 3, 5),
    ("2025", 4, 1, 3),
    ("2025", 4, 2, 4),
    ("2025", 4, 3, 3),
    ("2026", 5, 1, 1),
]

os.makedirs(ARTICLES_DIR, exist_ok=True)
os.makedirs(MERGED_DIR, exist_ok=True)
os.makedirs(CLEAN_DIR, exist_ok=True)

# ── STEP 1: Download all individual article PDFs ──
print("=" * 60)
print("STEP 1: Downloading individual article PDFs")
print("=" * 60)

total_articles = sum(n for _, _, _, n in ISSUES)
downloaded = 0
failed = []

for year, vol, iss, count in ISSUES:
    iss_dir = os.path.join(ARTICLES_DIR, f"v{vol}i{iss}")
    os.makedirs(iss_dir, exist_ok=True)
    for p in range(1, count + 1):
        fname = f"mt-v{vol}-n{iss}-p{p:02d}-s.pdf"
        path = os.path.join(iss_dir, fname)

        # Skip if already downloaded and valid
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            with open(path, "rb") as f:
                if f.read(5) == b"%PDF-":
                    downloaded += 1
                    continue

        url = f"https://msp.org/mt/{year}/{vol}-{iss}/{fname}"
        print(f"[{downloaded+1}/{total_articles}] {fname} ... ", end="", flush=True)

        result = subprocess.run(
            ["curl", "-sL", "-o", path, url],
            capture_output=True, text=True, timeout=120
        )

        sz = os.path.getsize(path) if os.path.exists(path) else 0
        if sz > 1000:
            with open(path, "rb") as f:
                hdr = f.read(8)
            if hdr[:5] == b"%PDF-":
                print(f"OK ({sz:,} bytes)")
                downloaded += 1
            else:
                print(f"NOT PDF (header={hdr[:20]}) — deleting")
                os.remove(path)
                failed.append((year, vol, iss, p))
        else:
            print(f"FAILED ({sz} bytes)")
            failed.append((year, vol, iss, p))

        time.sleep(0.3)  # gentle rate limit

print(f"\nDownloaded: {downloaded}/{total_articles}")
if failed:
    print(f"FAILED ({len(failed)}): {failed}")

# ── STEP 2: Merge per issue ──
print("\n" + "=" * 60)
print("STEP 2: Merging articles per issue")
print("=" * 60)

total_pages = 0

for year, vol, iss, count in ISSUES:
    iss_dir = os.path.join(ARTICLES_DIR, f"v{vol}i{iss}")
    out_name = f"{ISSN}V{vol}I{iss}.pdf"
    out_path = os.path.join(MERGED_DIR, out_name)

    # Collect articles in page order (p01, p02, ...)
    pdfs = []
    for p in range(1, count + 1):
        fname = f"mt-v{vol}-n{iss}-p{p:02d}-s.pdf"
        path = os.path.join(iss_dir, fname)
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            pdfs.append(path)

    if not pdfs:
        print(f"Vol {vol} Issue {iss}: NO PDFs found — skipping")
        continue

    print(f"Merging {len(pdfs)} articles → {out_name} ... ", end="", flush=True)

    writer = PdfWriter()
    issue_pages = 0
    for pdf_path in pdfs:
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            writer.add_page(page)
            issue_pages += 1

    with open(out_path, "wb") as f:
        writer.write(f)

    total_pages += issue_pages
    print(f"OK ({issue_pages} pages, {os.path.getsize(out_path):,} bytes)")

print(f"\nTotal pages across all issues: {total_pages}")

# ── STEP 3: Clean each merged issue ──
print("\n" + "=" * 60)
print("STEP 3: Cleaning merged PDFs")
print("=" * 60)

cleaner = os.path.join(BASE, "pdf_clean.py")
for f in sorted(os.listdir(MERGED_DIR)):
    if not f.endswith(".pdf"):
        continue
    src = os.path.join(MERGED_DIR, f)
    dst = os.path.join(CLEAN_DIR, f)
    print(f"Cleaning {f} ... ", end="", flush=True)
    result = subprocess.run(
        ["python", cleaner, src, "-o", dst],
        capture_output=True, text=True, timeout=300
    )
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        print(f"OK ({os.path.getsize(dst):,} bytes)")
    else:
        print(f"FAILED: {result.stderr[:200] if result.stderr else 'unknown'}")

print("\n" + "=" * 60)
print("DONE — cleaned issues in mt_clean/")
print(f"Issues: {len(os.listdir(CLEAN_DIR))} | Total pages: {total_pages}")
print("=" * 60)
