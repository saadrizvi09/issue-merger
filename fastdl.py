#!/usr/bin/env python3
"""
fastdl - fast journal issue downloader + merger.

Same idea as journaldl (resolve a journal via OpenAlex, grab open-access PDFs,
merge one PDF per issue) but rebuilt for SPEED:

  * Async, highly concurrent downloads (httpx) instead of a 6-thread pool that
    was serialized to ~3 req/s by a per-host rate limit. Every source caps at
    ~0.1 MB/s PER CONNECTION, so the only way to go fast is many connections.
  * No per-article Unpaywall / Europe-PMC API round-trips in the hot path -
    candidate URLs are built directly from data OpenAlex already returned
    (pdf_url + the publisher's own download_pdf.php?doi= endpoint). A single
    Europe-PMC fallback pass only runs for the few that still failed.
  * Merge with pikepdf (qpdf, C++): ~5x faster than pypdf. Thousands of pages
    merge in a couple of seconds.

Output: one merged, bookmarked PDF per issue:
    <out>/<Journal>/<Year>/<Journal>_<Year>_Vol<V>_Iss<I>_<N>arts.pdf

Resume: re-run any time. Valid per-article PDFs and already-merged issues are
skipped. Ctrl-C is safe.

Deps: httpx, pikepdf, requests  (reuses journaldl.py for journal resolution).
Python 3.11+.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx
import pikepdf
import requests

# Reuse the (already-hardened) config + journal resolution from journaldl.
import journaldl as J

OPENALEX = "https://api.openalex.org"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

KEEP_TYPES = J.KEEP_TYPES
JUNK_TITLE = J.JUNK_TITLE
log = logging.getLogger("fastdl")


# --------------------------------------------------------------------------- #
# Article record
# --------------------------------------------------------------------------- #
@dataclass
class Art:
    key: str
    doi: Optional[str]
    title: str
    year: Optional[int]
    volume: Optional[str]
    issue: Optional[str]
    first_page: str
    sort_key: str
    pdf_url: Optional[str]
    landing: Optional[str]
    pmcid: Optional[str] = None
    status: str = "pending"          # pending|done|no_pdf
    file: Optional[str] = None
    source: Optional[str] = None
    err: Optional[str] = None

    @property
    def fpkey(self) -> str:
        return J.safe(self.first_page or self.key)

    def candidates(self) -> list[str]:
        """Cheap candidate URLs, fastest/most-reliable first. No API calls."""
        out: list[str] = []
        def add(u):
            if u and u not in out:
                out.append(u)
        add(self.pdf_url)                                   # OpenAlex direct
        if self.pmcid:                                      # PMC mirror (Europe PMC, reliable)
            add(f"https://europepmc.org/articles/{self.pmcid}?pdf=render")
        if self.doi:                                        # Korean-publisher endpoint
            add(f"https://www.kjpp.net/journal/download_pdf.php?doi={self.doi}")
        return out


# --------------------------------------------------------------------------- #
# Enumerate articles via OpenAlex (sync, few cursor-paginated calls = fast)
# --------------------------------------------------------------------------- #
def enumerate_articles(cfg: J.Config, source_id: str,
                       years: Optional[tuple[int, int]],
                       keep_types: Optional[set] = None) -> list[Art]:
    def params(**extra):
        p = {"mailto": cfg.email, **extra}
        if cfg.api_key:
            p["api_key"] = cfg.api_key
        return p

    flt = f"primary_location.source.id:{source_id},is_paratext:false"
    if years:
        flt += f",publication_year:{years[0]}-{years[1]}"

    arts: list[Art] = []
    cursor = "*"
    with requests.Session() as s:
        s.headers["User-Agent"] = f"fastdl (mailto:{cfg.email})"
        while cursor:
            r = s.get(f"{OPENALEX}/works",
                      params=params(filter=flt, per_page=200, cursor=cursor),
                      timeout=cfg.timeout)
            r.raise_for_status()
            j = r.json()
            for w in j.get("results", []):
                a = _normalize(w, keep_types)
                if a:
                    arts.append(a)
            cursor = j.get("meta", {}).get("next_cursor")
    arts.sort(key=lambda a: a.sort_key)
    return arts


def _pmcid_from_locations(w: dict) -> Optional[str]:
    """Many diamond-OA journals (e.g. IJO on LWW) expose no best_oa_location
    pdf_url and no ids.pmcid, but DO carry a PubMed Central mirror in
    locations[]. Pull the PMCID out of that mirror's URL so we can fetch the
    real PDF from Europe PMC (NCBI's own /pdf/ endpoint is bot-walled)."""
    for loc in w.get("locations") or []:
        src = ((loc.get("source") or {}).get("display_name") or "")
        if "PubMed Central" not in src:
            continue
        for u in (loc.get("pdf_url"), loc.get("landing_page_url")):
            if not u:
                continue
            m = re.search(r"(PMC\d+)", u) or re.search(r"/articles/(\d+)", u)
            if m:
                g = m.group(1)
                return g if g.startswith("PMC") else f"PMC{g}"
    return None


def _normalize(w: dict, keep_types: Optional[set] = None) -> Optional[Art]:
    if keep_types is not None and w.get("type") not in keep_types:
        return None
    title = (w.get("title") or "").strip()
    if not title or JUNK_TITLE.match(title):
        return None
    bib = w.get("biblio") or {}
    loc = w.get("best_oa_location") or w.get("primary_location") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
    oa_id = w["id"].split("/")[-1]
    year = w.get("publication_year")
    vol = bib.get("volume")
    issue = bib.get("issue")
    fp = bib.get("first_page") or ""
    # Page number for ordering: parse the digits out of first_page. Many journals
    # use non-numeric folios like "e224" or "S12"; bare int(fp) fails on those and
    # collapses every article to 0 -> issues merge in the wrong (publication-date)
    # order. Strip non-digits so "e224" -> 224, giving correct printed-page order.
    fp_num = int(re.sub(r"\D", "", fp) or 0)
    pmcid = (w.get("ids") or {}).get("pmcid")
    if pmcid:
        pmcid = pmcid.rstrip("/").split("/")[-1]
    if not pmcid:
        pmcid = _pmcid_from_locations(w)
    sort_key = (f"{year or 0:04d}|{str(vol or ''):>8}|{str(issue or ''):>8}|"
                f"{fp_num:08d}|{w.get('publication_date','')}")
    return Art(
        key=doi or oa_id, doi=doi, title=title[:300], year=year,
        volume=vol, issue=issue, first_page=fp, sort_key=sort_key,
        pdf_url=loc.get("pdf_url"),
        landing=loc.get("landing_page_url")
                or (w.get("primary_location") or {}).get("landing_page_url"),
        pmcid=pmcid,
    )


# --------------------------------------------------------------------------- #
# PDF validation (fast: header + size + EOF marker, no full parse)
# --------------------------------------------------------------------------- #
def safe_unlink(p: Path, tries: int = 6) -> None:
    """Windows holds a file handle briefly after close (AV / indexer), so a plain
    unlink can raise WinError 32. Retry, then give up - a stray .part is harmless
    (unique per article now, and excluded from the Drive upload)."""
    for i in range(tries):
        try:
            p.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.15 * (i + 1))


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
# Async download (the speed win: many connections at once)
# --------------------------------------------------------------------------- #
async def download_all(arts: list[Art], pdf_dir: Path, conc: int,
                       timeout: int) -> None:
    sem = asyncio.Semaphore(conc)
    done = {"n": 0}
    total = len(arts)
    limits = httpx.Limits(max_connections=conc, max_keepalive_connections=conc)

    async with httpx.AsyncClient(http2=True, timeout=timeout, limits=limits,
                                 follow_redirects=True,
                                 headers={"User-Agent": UA,
                                          "Accept": "application/pdf,*/*"}) as client:
        async def one(a: Art):
            # Filename = the article's unique key (DOI/OpenAlex id), NOT first_page:
            # two articles in one issue can share a first page, which would collide
            # on the same .part file and race two async writers. Merge order is
            # decided by sort_key, so the filename only needs to be unique.
            dest = pdf_dir / J.safe(a.year) / f"Vol_{J.safe(a.volume)}" \
                   / f"Iss_{J.safe(a.issue)}" / f"{J.safe(a.key)}.pdf"
            if valid_pdf(dest):                       # resume: already have it
                a.status, a.file, a.source = "done", str(dest), "cache"
            else:
                async with sem:
                    await _fetch(client, a, dest)
            done["n"] += 1
            if done["n"] % 10 == 0 or done["n"] == total:
                got = sum(1 for x in arts if x.status == "done")
                log.info("  %d/%d processed (%d downloaded)", done["n"], total, got)

        await asyncio.gather(*(one(a) for a in arts))


async def _fetch(client: httpx.AsyncClient, a: Art, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    for url in a.candidates():
        try:
            ok = True
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    continue
                first = True
                with open(part, "wb") as fh:
                    async for chunk in r.aiter_bytes(65536):
                        if first:
                            if chunk[:5] != b"%PDF-":   # HTML bot wall / error page
                                ok = False
                                break
                            first = False
                        fh.write(chunk)
            if ok and valid_pdf(part):
                part.replace(dest)
                a.status, a.file, a.source = "done", str(dest), url
                return
            safe_unlink(part)
        except Exception as e:
            a.err = f"{type(e).__name__}: {e}"[:120]
            safe_unlink(part)
    a.status = "no_pdf"


# --------------------------------------------------------------------------- #
# Europe-PMC fallback (only for the few that failed; one search per item)
# --------------------------------------------------------------------------- #
def fill_pmcids(arts: list[Art], cfg: J.Config) -> int:
    failed = [a for a in arts if a.status == "no_pdf" and a.doi and not a.pmcid]
    if not failed:
        return 0
    log.info("Europe-PMC fallback: looking up %d missing PDFs ...", len(failed))
    n = 0
    with requests.Session() as s:
        s.headers["User-Agent"] = UA
        for a in failed:
            try:
                j = s.get(f"{EPMC}/search",
                          params={"query": f'DOI:"{a.doi}"', "format": "json",
                                  "resultType": "lite"}, timeout=cfg.timeout).json()
                hits = j.get("resultList", {}).get("result", [])
                if hits and hits[0].get("pmcid"):
                    a.pmcid = hits[0]["pmcid"]
                    a.status = "pending"
                    n += 1
            except Exception as e:
                log.debug("epmc lookup %s: %s", a.doi, e)
    return n


# --------------------------------------------------------------------------- #
# Merge one issue (pikepdf, with a bookmark per article)
# --------------------------------------------------------------------------- #
def merge_issue(jname: str, arts: list[Art], out_path: Path) -> Optional[tuple[Path, int, int]]:
    arts = [a for a in arts if a.file and valid_pdf(Path(a.file))]
    if not arts:
        return None
    out = pikepdf.Pdf.new()
    srcs = []
    with out.open_outline() as ol:
        for a in arts:
            try:
                src = pikepdf.open(a.file)
            except Exception as e:
                log.warning("merge skip %s: %s", a.file, e)
                continue
            srcs.append(src)
            start = len(out.pages)
            out.pages.extend(src.pages)
            ol.root.append(pikepdf.OutlineItem(a.title[:120], start))
    pages = len(out.pages)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path)
    out.close()
    for s in srcs:
        s.close()
    return out_path, len(arts), pages


def issue_filename(jname: str, a: Art, n: int) -> str:
    return f"V{J.safe(a.volume)}I{J.safe(a.issue)}Ar{n}.pdf"


def issue_glob(v, i) -> str:
    return f"V{J.safe(v)}I{J.safe(i)}Ar*.pdf"


# --------------------------------------------------------------------------- #
# Orchestrate
# --------------------------------------------------------------------------- #
def run(target: str, cfg: J.Config, years: Optional[tuple[int, int]],
        conc: int, keep: bool, force: bool, code: Optional[str] = None,
        keep_types: Optional[set] = None, drive_folder: Optional[str] = None,
        remote: str = "gdrive", flat_name: Optional[str] = None,
        issn_folder: bool = False, by_year: bool = False,
        label: Optional[str] = None) -> None:
    t0 = time.time()
    http = J.Http(cfg)
    src = J.OpenAlex(http, cfg).resolve_source(target)
    jname = src.get("display_name", "Journal")
    sid = src["id"].split("/")[-1]
    code = (code or "".join(c for c in jname if c.isupper()) or "JRNL").upper()
    if issn_folder and not flat_name:                 # --issn-folder: name the folder after the journal's ISSN
        issn = src.get("issn_l") or (src.get("issn") or [None])[0]
        flat_name = J.safe(issn) if issn else code
        log.info("Saving all merged PDFs into one folder named by ISSN: %s", flat_name)
    cache_dir = cfg.out / ".cache" / J.safe(code)     # per-article PDFs (never uploaded)
    log.info("Journal: %s | code=%s | OpenAlex %s | years=%s | concurrency=%d",
             jname, code, sid, f"{years[0]}-{years[1]}" if years else "all", conc)

    log.info("Enumerating articles via OpenAlex ...")
    arts = enumerate_articles(cfg, sid, years, keep_types)
    log.info("%d articles found (%d with a PMC mirror).",
             len(arts), sum(1 for a in arts if a.pmcid))

    from collections import Counter

    def issue_year(grp) -> str:
        yrs = [a.year for a in grp if a.year]
        return str(Counter(yrs).most_common(1)[0][0]) if yrs else "NA"

    if by_year:
        # --by-year: ONE merged PDF per calendar year (Jan-Dec), named
        # "<label> <year>.pdf", all in a single folder (--folder, default code).
        lab = label or code.lower()
        groups: dict[tuple, list[Art]] = {}
        for a in arts:
            groups.setdefault((str(a.year),), []).append(a)
        def grp_out_dir(key, grp) -> Path:
            return cfg.out / (flat_name or code)
        def grp_glob(key) -> str:
            return f"{lab} {key[0]}.pdf"
        def grp_filename(key, grp, n) -> str:
            return f"{lab} {key[0]}.pdf"
        def grp_sortkey(kv):
            return (kv[0][0],)
        unit = "year"
    else:
        # Group by (volume, issue). A calendar year spans two volumes (the volume
        # number rolls over near year-end), so grouping by year too would split a
        # single real issue across two year-folders. Instead we group by the issue
        # and file it under the dominant publication year of its own articles.
        groups = {}
        for a in arts:
            groups.setdefault((a.volume, a.issue), []).append(a)
        def grp_out_dir(key, grp) -> Path:
            # --folder NAME: all merged PDFs go flat into one folder (e.g. ISSN).
            # Otherwise one "<year> <code>" folder per year.
            if flat_name:
                return cfg.out / flat_name
            return cfg.out / f"{issue_year(grp)} {code}"
        def grp_glob(key) -> str:
            return issue_glob(key[0], key[1])
        def grp_filename(key, grp, n) -> str:
            return issue_filename(jname, grp[0], n)
        def grp_sortkey(kv):
            return (issue_year(kv[1]), str(kv[0][0]), str(kv[0][1]))
        unit = "issue"

    todo = arts
    if not force:
        done_keys = {k for k, g in groups.items()
                     if list(grp_out_dir(k, g).glob(grp_glob(k)))}
        todo = [a for k, g in groups.items() if k not in done_keys for a in g]
        if len(todo) < len(arts):
            log.info("Resume: %d %ss already merged, %d articles left to fetch.",
                     len(done_keys), unit, len(todo))

    if todo:
        log.info("Downloading (async, concurrency=%d) ...", conc)
        asyncio.run(download_all(todo, cache_dir, conc, cfg.timeout))
        # one fallback pass via Europe-PMC for anything still missing
        retry = [a for a in todo if a.status == "no_pdf"]
        if retry and fill_pmcids(retry, cfg):
            asyncio.run(download_all([a for a in retry if a.status == "pending"],
                                     cache_dir, conc, cfg.timeout))

    # merge each group (in printed page order) -> one PDF each
    log.info("Merging %ss with pikepdf ...", unit)
    merged_issues = merged_pages = 0
    for key, grp in sorted(groups.items(), key=grp_sortkey):
        grp.sort(key=lambda a: a.sort_key)
        out_dir = grp_out_dir(key, grp)
        existing = list(out_dir.glob(grp_glob(key)))
        if existing and not force:
            continue
        done_arts = [a for a in grp if a.file and valid_pdf(Path(a.file))]
        if not done_arts:
            continue
        out_path = out_dir / grp_filename(key, done_arts, len(done_arts))
        for old in existing:                      # replace stale merge
            old.unlink(missing_ok=True)
        res = merge_issue(jname, done_arts, out_path)
        if res:
            _, n, pages = res
            merged_issues += 1
            merged_pages += pages
            log.info("  %s/%s  (%d articles, %d pages)",
                     out_dir.name, out_path.name, n, pages)

    if not keep:
        import shutil
        shutil.rmtree(cache_dir, ignore_errors=True)

    n_done = sum(1 for a in arts if a.status == "done")
    n_nopdf = sum(1 for a in arts if a.status == "no_pdf")
    dt = time.time() - t0
    print("\n" + "=" * 60)
    print(f"  Journal            : {jname}  ({code})")
    print(f"  Years              : {f'{years[0]}-{years[1]}' if years else 'all'}")
    print(f"  Articles found     : {len(arts)}")
    print(f"  Downloaded (this run+cache): {n_done}")
    print(f"  No free PDF        : {n_nopdf}")
    print(f"  Issues merged      : {merged_issues}")
    print(f"  Pages merged       : {merged_pages}")
    print(f"  Output             : {cfg.out} (\"<year> {code}\" folders)")
    print(f"  Elapsed            : {dt:.1f}s")
    print("=" * 60)

    if drive_folder and merged_issues:
        try:
            import gdrive_sync
            log.info("Uploading merged PDFs to Google Drive ...")
            gdrive_sync.push(cfg.out, drive_folder, remote=remote)
        except Exception as e:
            log.error("Drive upload failed: %s (files are saved locally; "
                      "run gdrive_sync.py to retry)", e)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Fast journal downloader: one merged PDF per issue.")
    p.add_argument("target", help="journal ISSN, archive URL, or name")
    p.add_argument("--name", help="short journal code for folder/file names, e.g. IJO "
                                  "(default: the journal's capital letters)")
    p.add_argument("--years", help="year range, e.g. 2018-2026")
    p.add_argument("--types", default="research",
                   help="'research' (articles/reviews only, default) or 'all' "
                        "(also letters, editorials, errata - the whole issue)")
    p.add_argument("--drive", help="Google Drive folder ID: auto-upload merged PDFs when done")
    p.add_argument("--remote", default="gdrive", help="rclone remote name (default: gdrive)")
    p.add_argument("--folder", help="put ALL merged PDFs flat into one folder with this name")
    p.add_argument("--issn-folder", action="store_true",
                   help="put ALL merged PDFs flat into one folder named after the journal's ISSN")
    p.add_argument("--by-year", action="store_true",
                   help="merge ONE PDF per calendar year (Jan-Dec) instead of per issue")
    p.add_argument("--label",
                   help="filename prefix for --by-year PDFs, e.g. 'lsa' -> 'lsa 2020.pdf' "
                        "(default: lowercased journal code)")
    p.add_argument("--conc", type=int, default=32, help="concurrent downloads (default 32)")
    p.add_argument("--email", help="contact email (polite pool + Unpaywall)")
    p.add_argument("--api-key", help="free OpenAlex API key")
    p.add_argument("--out", help="output directory (default: Downloads)")
    p.add_argument("--no-keep", action="store_true",
                   help="delete per-article PDFs after merging (keeps only merged issues)")
    p.add_argument("--force", action="store_true",
                   help="re-download and re-merge even if issue PDFs already exist")
    p.add_argument("--config", help="path to config.json")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler("fastdl.log", encoding="utf-8")])

    years = None
    if args.years:
        lo, hi = re.split(r"[-:]", args.years)
        years = (int(lo), int(hi))

    config_path = args.config
    if not config_path:
        for cand in (Path("config.json"), Path(__file__).with_name("config.json")):
            if cand.exists():
                config_path = str(cand)
                log.info("Using config: %s", cand)
                break

    cfg = J.Config.load(config_path, email=args.email, api_key=args.api_key,
                        out=args.out)
    if cfg.email in ("you@example.com", "YOUR_REAL_EMAIL@example.com"):
        log.warning("Set a REAL email in config.json or --email (polite pool + Unpaywall).")

    keep_types = None if args.types.lower() == "all" else KEEP_TYPES
    run(args.target, cfg, years, args.conc, keep=not args.no_keep, force=args.force,
        code=args.name, keep_types=keep_types, drive_folder=args.drive,
        remote=args.remote, flat_name=args.folder, issn_folder=args.issn_folder,
        by_year=args.by_year, label=args.label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
