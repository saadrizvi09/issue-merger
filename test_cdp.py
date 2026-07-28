"""Test real Chrome CDP for T&F PDF access."""
import json, time, base64, re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DOI = sys.argv[1] if len(sys.argv) > 1 else "10.1080/00949655.2018.1430801"

with sync_playwright() as p:
    print(f"Connecting to Chrome CDP...")
    br = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = br.contexts[0]
    pg = ctx.new_page()

    # The Chrome might already have tabs open; grab a new one
    print(f"Existing pages: {len(ctx.pages)}")

    # Test 1: T&F journal homepage
    print(f"\n1. Journal homepage:")
    pg.goto("https://www.tandfonline.com/journals/gscs20", wait_until="domcontentloaded", timeout=30000)
    for i in range(10):
        t = pg.title()
        if "just a moment" not in t.lower():
            print(f"   OK: {t[:100]}")
            break
        time.sleep(1)
    else:
        print(f"   CF BLOCKED")

    # Test 2: Article page via DOI
    print(f"\n2. Article page: {DOI}")
    pg.goto(f"https://doi.org/{DOI}", wait_until="domcontentloaded", timeout=30000)
    for i in range(15):
        t = pg.title()
        if "just a moment" not in t.lower():
            print(f"   OK ({i+1}s): {t[:100]}")
            break
        time.sleep(1)
    else:
        print(f"   CF BLOCKED")

    html = pg.content()
    print(f"   URL: {pg.url}")
    print(f"   HTML size: {len(html):,} bytes")

    # Access indicators
    access = "unknown"
    for kw, label in [("View PDF", "PDF_LINK"), ("Download citation", "TOC_PAGE"),
                       ("Access restricted", "PAYWALL"), ("Log in", "LOGIN"),
                       ("Subscribe", "SUBSCRIBE"), ("Full Article", "FULL_ARTICLE"),
                       ("Open access", "OPEN_ACCESS"), ("citation_pdf_url", "PDF_META")]:
        c = html.lower().count(kw.lower())
        if c > 0:
            print(f"   [{label}] '{kw}' x{c}")

    # PDF URLs
    pdf_urls = re.findall(r'citation_pdf_url["\']?\s*content=["\']([^"\']+)["\']', html)
    pdf_urls += re.findall(r'href=["\'](/doi/pdf/[^"\']+)["\']', html)
    if pdf_urls:
        print(f"   PDF URLs: {set(pdf_urls)}")

    # Test 3: Direct PDF attempt
    print(f"\n3. Direct PDF download:")
    pdf_url = f"https://www.tandfonline.com/doi/pdf/{DOI}"
    resp = pg.goto(pdf_url, wait_until="commit", timeout=30000)
    if resp:
        ct = resp.headers.get('content-type', '')
        body = resp.body()
        print(f"   Status: {resp.status}")
        print(f"   Content-Type: {ct}")
        print(f"   Size: {len(body):,} bytes")
        print(f"   Is PDF: {body[:5] == b'%PDF-'}")
        if body[:5] == b'%PDF-':
            out = PROJECT / "real_chrome_test.pdf"
            out.write_bytes(body)
            print(f"   >>> SAVED: {out} ({len(body):,} bytes) <<<")
        elif len(body) < 200:
            print(f"   Body: {body}")
        else:
            print(f"   First 200 bytes: {body[:200]}")

    # Test 4: If paywalled, try the issue TOC page
    print(f"\n4. Issue TOC page:")
    pg.goto("https://www.tandfonline.com/toc/gscs20/88/10", wait_until="domcontentloaded", timeout=30000)
    for i in range(10):
        t = pg.title()
        if "just a moment" not in t.lower():
            print(f"   OK ({i+1}s): {t[:100]}")
            break
        time.sleep(1)
    else:
        print(f"   CF BLOCKED")

    pg.close()
    print("\nDone.")
