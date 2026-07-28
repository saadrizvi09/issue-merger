"""Test nodriver with proper API usage for Cloudflare bypass."""
import asyncio
import nodriver as nd
import re
from pathlib import Path

PROJECT = Path("C:/Projects/Automate pdf merge journal")

async def get_title(tab):
    """Get tab title safely."""
    try:
        # nodriver Tab may have .title as property or method
        if callable(getattr(tab, 'title', None)):
            return await tab.title()
        info = await tab.send(nd.cdp.target.get_target_info())
        return info.get('title', '')
    except:
        try:
            result = await tab.evaluate('document.title')
            return result
        except:
            return 'unknown'

async def get_html(tab):
    """Get tab HTML content."""
    try:
        return await tab.get_content()
    except:
        try:
            return await tab.evaluate('document.documentElement.outerHTML')
        except:
            return ''

async def main():
    browser = await nd.start(headless=True)

    try:
        # Test 1: Issue TOC page with Cloudflare bypass
        print("=== Test 1: T&F Issue TOC ===")
        tab = await browser.get("https://www.tandfonline.com/toc/gscs20/95/1")
        await asyncio.sleep(5)

        # Try Cloudflare bypass
        print("Checking for Cloudflare challenge...")
        html = await get_html(tab)
        if "just a moment" in html.lower():
            print("Cloudflare detected, using verify_cf...")
            await tab.verify_cf()
            await asyncio.sleep(3)

        title = await get_title(tab)
        print(f"Title: {title}")
        print(f"URL: {tab.url if hasattr(tab, 'url') else 'unknown'}")

        # Test 2: Article page
        print("\n=== Test 2: Article page ===")
        tab2 = await browser.get("https://doi.org/10.1080/00949655.2024.2424348")
        await asyncio.sleep(5)

        html2 = await get_html(tab2)
        if "just a moment" in html2.lower():
            print("Cloudflare detected on article page, using verify_cf...")
            await tab2.verify_cf()
            await asyncio.sleep(3)

        print(f"Title: {await get_title(tab2)}")

        # Check for access indicators
        html2 = await get_html(tab2)
        indicators = {
            "Access restricted": "PAYWALL",
            "purchase or subscription": "PAYWALL",
            "Log in": "PAYWALL",
            "You do not have access": "PAYWALL",
            "View PDF": "ACCESS",
            "Download citation": "TOC",
            "Full Article": "TOC",
            "citation_pdf_url": "PDF_META",
        }
        access_status = "UNKNOWN"
        for kw, status in indicators.items():
            if kw.lower() in html2.lower():
                count = html2.lower().count(kw.lower())
                print(f"  [{status}] '{kw}' found {count} times")
                if access_status == "UNKNOWN":
                    access_status = status

        # Look for PDF URLs
        pdf_urls = re.findall(r'citation_pdf_url["\']?\s*content=["\']([^"\']+)["\']', html2)
        pdf_urls += re.findall(r'href=["\']([^"\']*\.pdf[^"\']*)["\']', html2)
        pdf_urls += re.findall(r'href=["\'](/doi/pdf/[^"\']+)["\']', html2)
        print(f"  PDF URLs found: {len(pdf_urls)}")
        for pu in pdf_urls[:5]:
            print(f"    {pu[:150]}")

        # Test 3: Direct PDF download attempt
        if access_status == "ACCESS" or access_status == "PDF_META":
            print("\n=== Test 3: Direct PDF download ===")
            pdf_url = "https://www.tandfonline.com/doi/pdf/10.1080/00949655.2024.2424348"
            tab3 = await browser.get(pdf_url)
            await asyncio.sleep(5)
            html3 = await get_html(tab3)
            content_bytes = html3.encode() if isinstance(html3, str) else html3
            print(f"Content length: {len(content_bytes)}")
            print(f"Starts with PDF: {content_bytes[:5] == b'%PDF-'}")
            if content_bytes[:5] == b'%PDF-':
                out = PROJECT / "test_nodriver_pdf.pdf"
                out.write_bytes(content_bytes)
                print(f"SAVED PDF: {out} ({len(content_bytes):,} bytes)")

        print(f"\nAccess assessment: {access_status}")

    finally:
        browser.stop()

asyncio.run(main())
