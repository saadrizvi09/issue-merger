#!/usr/bin/env python3
"""
ojsdl - download + merge articles from an OJS / PKP journal issue page.

Many journals (IJMEX, and thousands of others) run on Open Journal Systems.
There the PDFs live on the journal site itself, not in OpenAlex, so we scrape
the issue page directly:

    issue/view/<id>           ->  sections ("Vol. 20, No. 1, (2026)") + articles
    article/view/<aid>        ->  galley id
    article/download/<aid>/<g> ->  the actual PDF

One merged, bookmarked PDF is produced per section (so "Vol. 20, No. 1" and
"No. 2" each become a single PDF). Downloads are async with retries (OJS hosts
are often slow/flaky), validated, and merged with pikepdf.

Usage:
    python ojsdl.py "https://ijmex.com/index.php/ijmex/issue/view/40" --sections 1,2

Deps: httpx, pikepdf, requests.  Python 3.11+.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pikepdf
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
log = logging.getLogger("ojsdl")


def safe(s) -> str:
    return re.sub(r"[^\w.-]+", "_", str(s)).strip("_") or "NA"


def valid_pdf(p: Path) -> bool:
    try:
        if p.stat().st_size < 1024:
            return False
        with open(p, "rb") as f:
            if f.read(5) != b"%PDF-":
                return False
            f.seek(-min(2048, p.stat().st_size), 2)
            return b"%%EOF" in f.read()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Scrape the issue page -> [(section_label, [(article_id, title), ...]), ...]
# --------------------------------------------------------------------------- #
# Matches issue/section headings like "Vol. 20, No. 1, (2026)", "Vol 33, No 3
# (2025)", or just "Vol 26 (2018)". "No" is optional so single-issue-per-page
# journals (one merged PDF per issue) and multi-section archive pages both work.
SECTION_RE = re.compile(r"<h[1-6][^>]*>\s*(Vol[.\s][^<]*?\d[^<]*?)\s*</h[1-6]>", re.I)
# A title link is article/view/<id>" (no /galley suffix). Capture its inner
# HTML (which may contain nested tags) and strip tags to get the title.
ARTICLE_RE = re.compile(r'article/view/(\d+)"[^>]*>(.*?)</a>', re.I | re.S)


ANY_ARTICLE_RE = re.compile(r"article/(?:view|download)/(\d+)", re.I)


def extract_articles(block: str) -> list[tuple[str, str]]:
    """Article (id, title) pairs in document order, deduped, nested-tag safe.

    Takes the ordered union of title links AND galley/download links, so an
    article whose title links straight to its PDF (no abstract page, hence no
    plain title anchor) is still captured.
    """
    titles: dict[str, str] = {}
    for aid, inner in ARTICLE_RE.findall(block):
        if aid not in titles:
            t = re.sub(r"<[^>]+>", " ", inner)           # drop nested markup
            titles[aid] = re.sub(r"\s+", " ", t).strip()
    out, seen = [], set()
    for m in ANY_ARTICLE_RE.finditer(block):
        aid = m.group(1)
        if aid not in seen:
            seen.add(aid)
            out.append((aid, titles.get(aid) or f"Article {aid}"))
    return out


def robust_get(session: requests.Session, url: str, tries: int = 4,
               timeout: int = 40) -> requests.Response | None:
    for i in range(tries):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
        except Exception as e:
            log.debug("get %s try %d: %s", url, i, e)
        time.sleep(2 * (i + 1))
    return None


def parse_issue(html: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """Split the issue HTML into sections, each with its articles in order."""
    out = []
    matches = list(SECTION_RE.finditer(html))
    if not matches:
        # no sections -> treat whole page as one block
        return [("", extract_articles(html))]
    for k, m in enumerate(matches):
        label = re.sub(r"\s+", " ", m.group(1)).strip()
        block = html[m.end(): matches[k + 1].start() if k + 1 < len(matches) else len(html)]
        out.append((label, extract_articles(block)))
    return out


def section_no(label: str) -> str | None:
    m = re.search(r"No\.?\s*(\d+)", label, re.I)
    return m.group(1) if m else None


def year_of(label: str) -> str | None:
    m = re.search(r"\((\d{4})\)", label) or re.search(r"\b(19|20)\d{2}\b", label)
    return m.group(0).strip("()") if m else None


def vol_no(label: str) -> str | None:
    m = re.search(r"Vol\.?\s*(\d+)", label, re.I)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# Async: per article, find galley + download the PDF
# --------------------------------------------------------------------------- #
async def fetch_article_pdf(client: httpx.AsyncClient, base: str, aid: str,
                            dest: Path, retries: int = 3) -> bool:
    if valid_pdf(dest):
        return True
    # 1. article page -> galley id(s)
    page = None
    for i in range(retries):
        try:
            r = await client.get(f"{base}/article/view/{aid}")
            if r.status_code == 200:
                page = r.text
                break
        except Exception as e:
            log.debug("article %s try %d: %s", aid, i, e)
        await asyncio.sleep(2 * (i + 1))
    if not page:
        return False
    galleys = []
    for g in re.findall(rf"article/(?:view|download)/{aid}/(\d+)", page):
        if g not in galleys:
            galleys.append(g)
    # 2. try each galley's download endpoint until one is a real PDF
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    for g in galleys:
        url = f"{base}/article/download/{aid}/{g}"
        for i in range(retries):
            try:
                ok = True
                async with client.stream("GET", url) as r:
                    if r.status_code != 200:
                        ok = False
                    else:
                        first = True
                        with open(part, "wb") as fh:
                            async for chunk in r.aiter_bytes(65536):
                                if first:
                                    if chunk[:5] != b"%PDF-":
                                        ok = False
                                        break
                                    first = False
                                fh.write(chunk)
                if ok and valid_pdf(part):
                    part.replace(dest)
                    return True
                part.unlink(missing_ok=True)
                break  # got a response but not a PDF -> try next galley
            except Exception as e:
                log.debug("download %s/%s try %d: %s", aid, g, i, e)
                part.unlink(missing_ok=True)
                await asyncio.sleep(2 * (i + 1))
    return False


async def download_section(base: str, arts: list[tuple[str, str]], pdf_dir: Path,
                           conc: int, timeout: int) -> dict[str, Path]:
    sem = asyncio.Semaphore(conc)
    results: dict[str, Path] = {}
    transport = httpx.AsyncHTTPTransport(retries=2)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 transport=transport,
                                 headers={"User-Agent": UA,
                                          "Accept": "application/pdf,*/*"}) as client:
        async def one(idx, aid, title):
            dest = pdf_dir / f"{idx:02d}_{aid}.pdf"
            async with sem:
                ok = await fetch_article_pdf(client, base, aid, dest)
            log.info("  [%s] %s  %s", "ok " if ok else "MISS", aid, title[:60])
            if ok:
                results[aid] = dest

        await asyncio.gather(*(one(i, aid, t) for i, (aid, t) in enumerate(arts)))
    return results


# --------------------------------------------------------------------------- #
# Merge a section -> one bookmarked PDF
# --------------------------------------------------------------------------- #
def merge(arts: list[tuple[str, str]], files: dict[str, Path],
          out_path: Path) -> tuple[int, int] | None:
    ordered = [(aid, title) for aid, title in arts if aid in files and valid_pdf(files[aid])]
    if not ordered:
        return None
    out = pikepdf.Pdf.new()
    srcs = []
    with out.open_outline() as ol:
        for aid, title in ordered:
            try:
                s = pikepdf.open(files[aid])
            except Exception as e:
                log.warning("merge skip %s: %s", aid, e)
                continue
            srcs.append(s)
            start = len(out.pages)
            out.pages.extend(s.pages)
            ol.root.append(pikepdf.OutlineItem(title[:120], start))
    pages = len(out.pages)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path)
    out.close()
    for s in srcs:
        s.close()
    return len(ordered), pages


# --------------------------------------------------------------------------- #
def run(issue_url: str, want_sections: set[str] | None, name: str | None,
        out_dir: Path, conc: int, timeout: int, keep: bool,
        drive_folder: str | None = None, remote: str = "gdrive") -> None:
    t0 = time.time()
    sp = urlsplit(issue_url)
    base = f"{sp.scheme}://{sp.netloc}" + sp.path.split("/issue/")[0]
    jcode = name or sp.path.strip("/").split("/")[1] if "/index.php/" in sp.path else sp.netloc
    jcode = safe(jcode).upper()

    s = requests.Session()
    s.headers["User-Agent"] = UA
    log.info("Fetching issue page: %s", issue_url)
    r = robust_get(s, issue_url)
    if not r:
        sys.exit(f"Could not load issue page: {issue_url}")
    sections = parse_issue(r.text)
    log.info("Found %d section(s):", len(sections))
    for label, arts in sections:
        log.info("   %-28s %d articles", label or "(unlabeled)", len(arts))

    targets = []
    for label, arts in sections:
        no = section_no(label)
        if want_sections and (no not in want_sections):
            continue
        if not arts:
            continue
        targets.append((label, no, vol_no(label), arts))
    if not targets:
        sys.exit("No matching sections to download.")

    total_issues = total_pages = 0
    for label, no, vol, arts in targets:
        log.info("\n=== %s : downloading %d articles (conc=%d) ===", label, len(arts), conc)
        # per-article PDFs go in a hidden cache (never uploaded); merged PDF
        # goes in a "<YEAR> <JOURNAL>" folder that mirrors the Drive layout.
        pdf_dir = out_dir / ".cache" / jcode / safe(label)
        files = asyncio.run(download_section(base, arts, pdf_dir, conc, timeout))
        year = year_of(label) or "NA"
        fn = f"V{vol or 'NA'}I{no or '1'}Ar{len(files)}.pdf"
        out_path = out_dir / f"{year} {jcode}" / fn
        res = merge(arts, files, out_path)
        if res:
            n, pages = res
            total_issues += 1
            total_pages += pages
            log.info("MERGED -> %s/%s  (%d/%d articles, %d pages)",
                     out_path.parent.name, out_path.name, n, len(arts), pages)
            if not keep:
                import shutil
                shutil.rmtree(pdf_dir, ignore_errors=True)
        else:
            log.warning("No PDFs obtained for %s", label)

    if drive_folder and total_issues:
        import gdrive_sync
        gdrive_sync.push(out_dir, drive_folder, remote)

    dt = time.time() - t0
    print("\n" + "=" * 60)
    print(f"  Journal       : {jcode}")
    print(f"  Sections done : {total_issues}")
    print(f"  Pages merged  : {total_pages}")
    print(f"  Output        : {out_dir}")
    if drive_folder:
        print(f"  Drive folder  : {drive_folder}")
    print(f"  Elapsed       : {dt:.1f}s")
    print("=" * 60)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Download + merge an OJS journal issue per section.")
    p.add_argument("issue_url", help="OJS issue page URL, e.g. .../issue/view/40")
    p.add_argument("--sections", help="comma list of issue numbers to grab, e.g. 1,2 (default: all)")
    p.add_argument("--name", help="short journal code for filenames (default: from URL)")
    p.add_argument("--out", default="Downloads", help="output directory")
    p.add_argument("--conc", type=int, default=6, help="concurrent downloads (default 6; OJS hosts are flaky)")
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--no-keep", action="store_true", help="delete per-article PDFs after merge")
    p.add_argument("--drive", help="Google Drive folder ID to upload merged PDFs to (via rclone)")
    p.add_argument("--remote", default="gdrive", help="rclone remote name (default: gdrive)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler("ojsdl.log", encoding="utf-8")])

    want = set(x.strip() for x in args.sections.split(",")) if args.sections else None
    run(args.issue_url, want, args.name, Path(args.out), args.conc, args.timeout,
        keep=not args.no_keep, drive_folder=args.drive, remote=args.remote)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
