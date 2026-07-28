"""
CDP Chrome-based deep research — monitors T&F network traffic to find PDF endpoints.
Uses the real Chrome that bypasses Cloudflare.
"""
import json, re, time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT = Path("C:/Projects/Automate pdf merge journal")
DOI_OA = "10.1080/00949655.2022.2125971"      # CC-BY
DOI_PW = "10.1080/00949655.2018.1430801"      # paywalled
CDP = "http://127.0.0.1:9222"

def wait_cf(page, m=15):
    for _ in range(m):
        if "just a moment" not in page.title().lower():
            return True
        time.sleep(1)
    return False

with sync_playwright() as p:
    br = p.chromium.connect_over_cdp(CDP)
    ctx = br.contexts[0]
    pg = ctx.new_page()

    requests_log = []
    def log_req(request):
        url = request.url
        if any(x in url.lower() for x in ['pdf', 'epdf', 'epub', '/action/', 'literatum',
                                            '/doi/', 'api', 'download']):
            requests_log.append({
                'url': url[:250], 'method': request.method,
                'type': request.resource_type,
            })
    pg.on('request', log_req)

    # ---- Phase 1: OA Article ----
    print("=== PHASE 1: OA Article (CC-BY) ===")
    pg.goto("https://www.tandfonline.com/journals/gscs20", wait_until="domcontentloaded", timeout=30000)
    wait_cf(pg)
    requests_log.clear()

    pg.goto(f"https://doi.org/{DOI_OA}", wait_until="domcontentloaded", timeout=30000)
    wait_cf(pg)
    time.sleep(3)
    print(f"Title: {pg.title()[:100]}")
    print(f"URL: {pg.url}")

    # Print all interesting requests
    interesting = [r for r in requests_log if any(x in r['url'].lower()
                   for x in ['pdf', 'epdf', 'epub', '/action/', 'api'])]
    print(f"\nAPI/PDF requests ({len(interesting)}):")
    for r in interesting:
        print(f"  [{r['type']:12s}] {r['method']} {r['url'][:200]}")

    # Browser fetch the PDF
    result = pg.evaluate("""
        async (url) => {
            const r = await fetch(url, {credentials: 'include', redirect: 'follow'});
            const ct = r.headers.get('content-type') || '';
            return {ok: r.ok, status: r.status, url: r.url, ct: ct};
        }
    """, f"https://www.tandfonline.com/doi/pdf/{DOI_OA}")
    print(f"\nOA PDF fetch: ok={result.get('ok')}, status={result.get('status')}")
    print(f"  Final URL: {result.get('url','')[:200]}")
    print(f"  Content-Type: {result.get('ct','')[:80]}")

    # ---- Phase 2: Paywalled Article ----
    print(f"\n=== PHASE 2: Paywalled Article ===")
    requests_log.clear()

    pg.goto(f"https://doi.org/{DOI_PW}", wait_until="domcontentloaded", timeout=30000)
    wait_cf(pg)
    time.sleep(3)
    print(f"Title: {pg.title()[:100]}")
    html = pg.content()

    # Find ALL API endpoints and hidden links
    api_patterns = {
        "citation_pdf_url": r'citation_pdf_url["\']?\s*content=["\']([^"\']+)["\']',
        "showPdf": r'showPdf["\']?\s*:\s*["\']([^"\']+)["\']',
        "data-url": r'data-url\s*=\s*["\']([^"\']+)["\']',
        "literatum endpoint": r'(/action/[a-zA-Z]+[^"\'\s]{0,80})',
        "pdf href": r'href=["\']([^"\']*pdf[^"\']*)["\']',
        "epdf href": r'href=["\']([^"\']*epdf[^"\']*)["\']',
        "download href": r'href=["\']([^"\']*download[^"\']*)["\']',
        "doi/pdf href": r'href=["\']([^"\']*doi/pdf[^"\']*)["\']',
    }

    print("\nFound patterns in HTML:")
    for name, pattern in api_patterns.items():
        matches = re.findall(pattern, html, re.IGNORECASE)
        if matches:
            unique = list(set(matches))[:5]
            print(f"  [{name}]: {unique}")

    # Check for access token or session data
    for pat in [r'accessToken["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'bearer["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'csrf["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                r'window\.__INITIAL_STATE__\s*=\s*({[^;]+})']:
        matches = re.findall(pat, html)
        if matches:
            print(f"  [auth data]: {str(matches[0])[:120]}")

    # ---- Phase 3: Direct /doi/pdf/ fetch ----
    print(f"\n=== Phase 3: Direct PDF fetch attempts ===")
    for label, url in [
        ("pdf", f"https://www.tandfonline.com/doi/pdf/{DOI_PW}"),
        ("pdf?download", f"https://www.tandfonline.com/doi/pdf/{DOI_PW}?download=true"),
        ("epdf", f"https://www.tandfonline.com/doi/epdf/{DOI_PW}"),
        ("pdfdirect", f"https://www.tandfonline.com/doi/pdfdirect/{DOI_PW}"),
    ]:
        resp = pg.goto(url, wait_until="commit", timeout=15000)
        if resp:
            body = resp.body()
            ct = resp.headers.get('content-type', '?')
            is_pdf = len(body) > 10000 and body[:5] == b'%PDF-'
            print(f"  [{label:15s}] HTTP {resp.status}, {len(body):,}b, {ct[:50]}{' *** PDF ***' if is_pdf else ''}")

    # ---- Phase 4: Try from article page context ----
    print(f"\n=== Phase 4: From article page context ===")
    pg.goto(f"https://doi.org/{DOI_PW}", wait_until="domcontentloaded", timeout=30000)
    wait_cf(pg)
    time.sleep(2)

    fetch_result = pg.evaluate("""
        async (url) => {
            const r = await fetch(url, {credentials: 'include', redirect: 'follow'});
            const ct = r.headers.get('content-type') || '';
            const buf = await r.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let bin = '';
            for (let i = 0; i < bytes.length; i += 32768)
                bin += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i+32768, bytes.length)));
            return {ok: r.ok, status: r.status, ct: ct, len: bytes.length, b64: btoa(bin), url: r.url};
        }
    """, f"https://www.tandfonline.com/doi/pdf/{DOI_PW}")

    is_pdf = fetch_result.get('len', 0) > 10000
    if is_pdf:
        import base64
        data = base64.b64decode(fetch_result['b64'])
        is_pdf = data[:5] == b'%PDF-'
    print(f"  ok={fetch_result.get('ok')}, status={fetch_result.get('status')}")
    print(f"  content-type: {fetch_result.get('ct','?')[:80]}")
    print(f"  size: {fetch_result.get('len', 0):,} bytes")
    print(f"  final URL: {fetch_result.get('url','')[:200]}")
    print(f"  is PDF: {is_pdf}")

    pg.close()
    print("\nDone.")
