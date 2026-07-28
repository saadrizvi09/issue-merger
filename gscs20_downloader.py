"""
GSCS20 DEFINITIVE 100% PDF DOWNLOAD SYSTEM

Strategy:
1. Open VISIBLE Chrome → auto-navigate to JMI institutional login
2. User logs in ONCE
3. System auto-detects auth and downloads ALL 1,607 PDFs
4. Each issue merged → cleaned → ready for Drive upload

ONE login. 100% complete PDFs. Zero manual work after login.
"""
import json, sys, time, base64, re, os
from pathlib import Path
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter
from collections import defaultdict
import subprocess

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DOWNLOADS_DIR = PROJECT / "gscs20_downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
MERGED_DIR = PROJECT / "gscs20_merged"
MERGED_DIR.mkdir(exist_ok=True)
CLEAN_DIR = PROJECT / "gscs20_clean"
CLEAN_DIR.mkdir(exist_ok=True)
DOIS_FILE = PROJECT / "gscs20_dois.json"
PROGRESS_FILE = PROJECT / "gscs20_auth_progress.json"
COOKIES_FILE = PROJECT / "gscs20_cookies.json"
CLEANER = PROJECT / "pdf_clean.py"
CDP_URL = "http://127.0.0.1:9222"

ISSN = "00949655"
JOURNAL = "Journal of Statistical Computation and Simulation"

# ── helpers ──

def safe(s):
    return re.sub(r'[^A-Za-z0-9._-]', '_', s)

def load_json(path):
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {}

def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

def load_progress():
    p = load_json(PROGRESS_FILE)
    p.setdefault("downloaded", {})
    p.setdefault("failed", [])
    p.setdefault("merged", [])
    p.setdefault("cleaned", [])
    return p

def save_progress(p):
    save_json(PROGRESS_FILE, p)

def wait_cf(page, m=15):
    for _ in range(m):
        if "just a moment" not in page.title().lower():
            return True
        time.sleep(1)
    return False

def fetch_pdf(page, doi):
    """Download PDF using authenticated browser fetch."""
    result = page.evaluate("""
        async (url) => {
            try {
                const r = await fetch(url, {
                    credentials: 'include', redirect: 'follow',
                    headers: {'Accept': 'application/pdf,*/*'}
                });
                const ct = r.headers.get('content-type') || '';
                const buf = new Uint8Array(await r.arrayBuffer());
                let bin = '';
                const C = 0x8000;
                for (let i = 0; i < buf.length; i += C)
                    bin += String.fromCharCode.apply(null, buf.subarray(i, Math.min(i+C, buf.length)));
                return {ok: true, len: buf.length, b64: btoa(bin), ct};
            } catch(e) { return {ok: false, error: e.message}; }
        }
    """, f"https://www.tandfonline.com/doi/pdf/{doi}")
    if result.get("ok") and result.get("len", 0) > 10000:
        data = base64.b64decode(result["b64"])
        if data[:5] == b"%PDF-":
            return data
    return None

def check_access(page):
    """Check if we have institutional T&F access."""
    doi = "10.1080/00949655.2018.1430801"
    page.goto(f"https://doi.org/{doi}", wait_until="domcontentloaded", timeout=30000)
    wait_cf(page)
    time.sleep(1)

    title = page.title()
    pdf_data = fetch_pdf(page, doi)

    if pdf_data:
        return "full", pdf_data
    elif "Get Access" in title:
        return "none", None
    elif "Full article" in title.lower():
        return "html", None
    else:
        return "unknown", None

# ── login flow ──

def do_institutional_login(page):
    """Guide user through JMI institutional login. Returns True on success."""
    print("\n" + "=" * 70)
    print("  JMI INSTITUTIONAL LOGIN")
    print("=" * 70)
    print("""
  A Chrome window will open at the T&F institutional login page.

  1. Find "Jamia Millia Islamia" in the institution list
  2. Click it -> you'll be redirected to JMI's login
  3. Log in with your JMI credentials
  4. After login, you'll return to T&F (showing your name)

  The system will AUTO-DETECT when you're logged in.
  """)

    # Go to T&F login page
    page.goto("https://www.tandfonline.com/action/showLogin?redirectUri=%2F",
              wait_until="domcontentloaded", timeout=30000)
    wait_cf(page)
    time.sleep(2)

    # Click "Log in via your institution" or find Shibboleth
    html = page.content()

    # Try to find and click institutional login
    try:
        inst_btn = page.locator('a:has-text("institution")').first
        if inst_btn.count() > 0:
            inst_btn.click(timeout=5000)
            time.sleep(2)
    except:
        pass

    # Navigate to T&F Shibboleth wayfinder
    page.goto("https://www.tandfonline.com/action/ssostart",
              wait_until="domcontentloaded", timeout=30000)
    wait_cf(page)
    time.sleep(2)

    print(f"  Current page: {page.title()[:80]}")
    print(f"  URL: {page.url[:120]}")
    print()
    print("  >>> LOG IN NOW through the Chrome window <<<")
    print("  >>> This script will wait until you're authenticated <<<")
    print()

    # Wait for login to complete (poll every 3 seconds, max 5 minutes)
    for i in range(100):
        time.sleep(3)
        try:
            current_url = page.url
            current_title = page.title()

            # Check if we're back on T&F
            if "tandfonline.com" in current_url:
                # Check if we're logged in (no "showLogin" in URL, no "Log in" page)
                if "showLogin" not in current_url and "wayfinder" not in current_url:
                    access, pdf = check_access(page)
                    if access == "full":
                        print(f"\n  >>> LOGIN DETECTED! Full PDF access confirmed. <<<")
                        return True
                    elif access == "none":
                        # Might need to click through
                        if "Get Access" not in current_title:
                            print(f"  [{i*3}s] On T&F but not fully authenticated...")
                    else:
                        print(f"  [{i*3}s] On T&F ({current_title[:60]}...)")

            # Check if we're on JMI login page
            if "jmi" in current_url.lower() or "jamia" in current_url.lower():
                if i == 0 or i % 10 == 0:
                    print(f"  [{i*3}s] On JMI login page - waiting for you to log in...")

            # If we seem stuck, print periodic status
            if i % 20 == 0 and i > 0:
                print(f"  [{i*3}s] Still waiting... Current: {current_title[:80]}")

        except Exception as e:
            print(f"  [{i*3}s] Poll error: {e}")

    print("\n  Login timeout (5 min). Please try again.")
    return False

# ── main download ──

def download_all_articles(page, doi_data, progress):
    """Download ALL articles from all issues."""
    issues = doi_data.get("issues", {})
    downloaded = progress.get("downloaded", {})

    # Build work list
    work = []
    for issue_key in sorted(issues.keys()):
        for art in issues[issue_key]:
            doi = art.get("doi", "")
            if doi and doi not in downloaded:
                work.append(art)

    total = len(downloaded) + len(work)
    print(f"\nTotal articles: {total}")
    print(f"Already downloaded: {len(downloaded)}")
    print(f"To download: {len(work)}")

    if not work:
        print("All articles already downloaded!")
        return

    stats = {"ok": 0, "fail": 0}
    batch_start = time.time()

    for idx, art in enumerate(work):
        doi = art["doi"]
        vol = art.get("volume", "?")
        iss = art.get("issue", "?")
        title_safe = (art.get("title", "?"))[:80].encode('ascii', errors='replace').decode('ascii')

        # Handle unknown vol/issue
        vol_dir = vol if vol != "?" else "0"
        iss_dir = iss if iss != "?" else "0"

        issue_dir = DOWNLOADS_DIR / f"V{vol_dir}I{iss_dir}"
        issue_dir.mkdir(exist_ok=True)
        fpath = issue_dir / f"{safe(doi)}.pdf"

        # Skip if already downloaded successfully
        if fpath.exists() and fpath.stat().st_size > 10000:
            with open(fpath, 'rb') as f:
                if f.read(5) == b'%PDF-':
                    downloaded[doi] = {"volume": vol_dir, "issue": iss_dir, "size": fpath.stat().st_size}
                    stats["ok"] += 1
                    continue

        n = idx + 1
        elapsed = time.time() - batch_start
        rate = n / elapsed if elapsed > 0 else 0

        print(f"[{n:4d}/{len(work)}] V{vol}I{iss} | {doi[:45]}... | {rate:.1f}/s", end=" ", flush=True)

        try:
            page.goto(f"https://doi.org/{doi}", wait_until="domcontentloaded", timeout=30000)
            if not wait_cf(page):
                print("CF_BLOCK")
                stats["fail"] += 1
                progress["failed"].append(doi)
                continue

            time.sleep(0.5)
            pdf_data = fetch_pdf(page, doi)

            if pdf_data and len(pdf_data) > 10000 and pdf_data[:5] == b"%PDF-":
                fpath.write_bytes(pdf_data)
                sz = len(pdf_data)
                print(f"OK {sz//1024}KB")
                downloaded[doi] = {"volume": vol_dir, "issue": iss_dir, "size": sz}
                stats["ok"] += 1
            else:
                print("FAIL")
                stats["fail"] += 1
                progress["failed"].append(doi)

        except Exception as e:
            print(f"ERR:{str(e)[:30]}")
            stats["fail"] += 1
            progress["failed"].append(doi)

        # Save progress every 20 articles
        if n % 20 == 0:
            progress["downloaded"] = downloaded
            save_progress(progress)
            elapsed = time.time() - batch_start
            eta = (len(work) - n) / rate if rate > 0 else 0
            print(f"  [{n}/{len(work)}] ok={stats['ok']} fail={stats['fail']} ETA={eta/60:.0f}m")

    progress["downloaded"] = downloaded
    save_progress(progress)

    print(f"\nDownload phase complete: {stats['ok']} ok, {stats['fail']} failed")
    return stats

# ── merge & clean ──

def merge_and_clean(progress):
    """Merge per-issue, then clean each merged PDF."""
    doi_data = load_json(DOIS_FILE)
    issues = doi_data.get("issues", {})
    downloaded = progress.get("downloaded", {})

    print(f"\n{'='*60}")
    print("MERGING & CLEANING")
    print(f"{'='*60}")

    total_issues = 0
    total_pages = 0

    for issue_key in sorted(issues.keys()):
        arts = issues[issue_key]
        if not arts:
            continue

        vol = arts[0].get("volume", "?")
        iss = arts[0].get("issue", "?")
        vol_dir = vol if vol != "?" else "0"
        iss_dir = iss if iss != "?" else "0"

        issue_dir = DOWNLOADS_DIR / f"V{vol_dir}I{iss_dir}"
        pdfs = sorted(issue_dir.glob("*.pdf"))

        # Only consider downloaded PDFs
        dl_pdfs = []
        for pdf in pdfs:
            # Extract DOI from filename and check it's in downloaded
            for doi in downloaded:
                if safe(doi) in pdf.name:
                    dl_pdfs.append(pdf)
                    break
            else:
                if pdf.stat().st_size > 10000:
                    dl_pdfs.append(pdf)

        if not dl_pdfs:
            continue

        # Build article count from Crossref data for page ordering
        # Articles are sorted by page number
        out_name = f"{ISSN}V{vol}I{iss}.pdf"
        out_merged = MERGED_DIR / out_name

        if out_merged.exists() and out_merged.stat().st_size > 10000:
            skip = True
            # Check if any new PDFs were added
            merged_mtime = out_merged.stat().st_mtime
            for p in dl_pdfs:
                if p.stat().st_mtime > merged_mtime:
                    skip = False
                    break
            if skip:
                print(f"  {out_name}: already merged ({len(dl_pdfs)} articles)")
                total_issues += 1
                continue

        # Merge
        print(f"  Merging {out_name}: {len(dl_pdfs)} articles...", end=" ", flush=True)
        writer = PdfWriter()
        pages = 0
        for pdf_path in dl_pdfs:
            try:
                reader = __import__('pypdf').PdfReader(str(pdf_path))
                for page in reader.pages:
                    writer.add_page(page)
                    pages += 1
            except Exception as e:
                print(f"(err:{pdf_path.name[:20]})", end=" ")

        writer.write(str(out_merged))
        total_issues += 1
        total_pages += pages
        print(f"{pages}p, {out_merged.stat().st_size:,}b")

        # Clean
        out_clean = CLEAN_DIR / out_name
        if CLEANER.exists():
            result = subprocess.run(
                ["python", str(CLEANER), str(out_merged), "-o", str(out_clean)],
                capture_output=True, text=True, timeout=300
            )
            if out_clean.exists():
                print(f"    Cleaned: {out_clean.stat().st_size:,}b")
        else:
            # Copy if cleaner missing
            import shutil
            shutil.copy(out_merged, out_clean)
            print(f"    Copied to clean (no cleaner available)")

    print(f"\nMerged: {total_issues} issues, {total_pages} total pages")
    return total_issues, total_pages


# ── main ──

def main():
    doi_data = load_json(DOIS_FILE)
    if not doi_data:
        print(f"ERROR: {DOIS_FILE} not found!")
        sys.exit(1)

    progress = load_progress()

    print(f"Journal: {JOURNAL}  |  ISSN: {ISSN}")
    total_articles = sum(len(a) for a in doi_data.get("issues", {}).values())
    dl_count = len(progress.get("downloaded", {}))
    print(f"Articles: {total_articles} total, {dl_count} downloaded, {len(progress.get('failed', []))} failed")

    # Connect to CDP Chrome
    print(f"\nConnecting to Chrome: {CDP_URL}")
    try:
        with sync_playwright() as p:
            br = p.chromium.connect_over_cdp(CDP_URL)
            ctx = br.contexts[0]
            page = ctx.new_page()

            # Check access
            print("Checking T&F access...")
            access_level, _ = check_access(page)

            if access_level == "full":
                print(">>> ALREADY AUTHENTICATED - Full PDF access! <<<")
            elif access_level == "none":
                print("Not authenticated. Starting institutional login...")
                if not do_institutional_login(page):
                    print("Login failed or timed out.")
                    page.close()
                    return

            # WARM UP: navigate T&F homepage to load session
            page.goto("https://www.tandfonline.com/journals/gscs20",
                      wait_until="domcontentloaded", timeout=30000)
            wait_cf(page)
            print(f"Session ready: {page.title()[:80]}")

            # DOWNLOAD ALL
            stats = download_all_articles(page, doi_data, progress)
            page.close()

    except Exception as e:
        print(f"ERROR connecting to Chrome: {e}")
        print(f"\nPlease make sure Chrome is running with:")
        print(f'  chrome.exe --remote-debugging-port=9222 --user-data-dir="..."')
        sys.exit(1)

    # MERGE & CLEAN
    merge_and_clean(progress)

    # Final report
    p = load_progress()
    dl = p.get("downloaded", {})
    failed = p.get("failed", [])

    pdfs = list(DOWNLOADS_DIR.rglob("*.pdf"))
    total_size = sum(p.stat().st_size for p in pdfs)
    cleaned_pdfs = list(CLEAN_DIR.glob("*.pdf"))

    print(f"\n{'='*70}")
    print(f"FINAL REPORT: {JOURNAL}")
    print(f"{'='*70}")
    print(f"  Articles downloaded: {len(dl)}/{total_articles} ({len(dl)/total_articles*100:.1f}%)")
    print(f"  Failed: {len(failed)}")
    print(f"  PDFs on disk: {len(pdfs)} ({total_size/1024/1024:.0f} MB)")
    print(f"  Merged issues: {len(list(MERGED_DIR.glob('*.pdf')))}")
    print(f"  Cleaned issues: {len(cleaned_pdfs)}")
    print(f"  Output directories:")
    print(f"    Downloads: {DOWNLOADS_DIR}")
    print(f"    Merged:    {MERGED_DIR}")
    print(f"    Cleaned:   {CLEAN_DIR}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
