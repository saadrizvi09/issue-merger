#!/usr/bin/env python3
"""
journaldl - a resilient, publisher-agnostic academic journal PDF downloader.

Design (metadata-first):
  journal URL / ISSN / name  ->  ISSN  ->  OpenAlex source id
  OpenAlex  ->  full article list (cursor-paginated, paratext/editorials filtered)
  per article: resolve a downloadable PDF via a fallback chain
      OpenAlex pdf_url  ->  Unpaywall  ->  Europe PMC / PMC  ->  landing-page <meta>
  download (streaming, retried, atomic, validated) -> merge per issue (bookmarked)

Resilience contract ("never fails" = never crashes, never loses work):
  * Every article is processed in isolation; one failure never aborts the run.
  * All progress is persisted to SQLite; re-running resumes and only retries
    pending/failed items. Ctrl-C checkpoints and exits cleanly.
  * Downloads are atomic (.part -> rename) and validated (PDF magic + parse)
    so a half-downloaded or HTML "bot wall" file is never treated as success.

Not magic: PDFs that are paywalled or behind bot protection (e.g. Karger,
Elsevier on direct hits) are recorded as `no_free_pdf` / `blocked`, not invented.
Use --browser (Playwright, optional) to defeat bot walls on OA content.

Deps: requests, pypdf, tqdm  (Playwright optional, only for --browser).
Python 3.11+.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import html as _html
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PyPdfError as PdfError
except Exception:  # pragma: no cover
    print("pypdf is required: pip install pypdf", file=sys.stderr)
    raise

try:
    from tqdm import tqdm
except Exception:  # tqdm optional; degrade to a no-op
    def tqdm(it=None, **k):
        return it if it is not None else _Noop()
    class _Noop:
        def update(self, *_a, **_k): ...
        def close(self): ...
        def __enter__(self): return self
        def __exit__(self, *_a): ...

OPENALEX = "https://api.openalex.org"
UNPAYWALL = "https://api.unpaywall.org/v2"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Article types we keep (research content). Everything else (paratext, editorial,
# erratum, letter, ...) is skipped so issues only contain real articles.
KEEP_TYPES = {"article", "review", "preprint", "report", "book-chapter"}
# Belt-and-suspenders title filter for front/back matter that slips through.
JUNK_TITLE = re.compile(
    r"^\s*(front\s*matter|back\s*matter|table\s*of\s*contents|contents|"
    r"editorial\s*board|masthead|cover|prelims|acknowledg|index|"
    r"instructions?\s+for\s+authors|author\s+index|issue\s+information)\b",
    re.I,
)

log = logging.getLogger("journaldl")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    email: str = "you@example.com"          # polite-pool + Unpaywall (use a real one)
    api_key: Optional[str] = None           # free OpenAlex key -> 100k calls/day
    out: Path = Path("Downloads")
    threads: int = 6
    timeout: int = 45
    retries: int = 5                        # network retries per request
    rate_limit: float = 0.2                 # min seconds between requests per host
    merge: bool = True
    browser: bool = False                   # use Playwright for bot-walled hosts
    years: Optional[tuple[int, int]] = None # inclusive (lo, hi) filter
    limit: Optional[int] = None             # cap #articles (for testing)

    @classmethod
    def load(cls, path: Optional[str], **overrides) -> "Config":
        data: dict = {}
        if path and Path(path).exists():
            data = json.loads(Path(path).read_text())
        data.update({k: v for k, v in overrides.items() if v is not None})
        if "out" in data:
            data["out"] = Path(data["out"])
        if isinstance(data.get("years"), list):
            data["years"] = tuple(data["years"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# --------------------------------------------------------------------------- #
# Rate-limited, retrying HTTP session (one shared pool)
# --------------------------------------------------------------------------- #
class Http:
    """Thread-safe HTTP helper: connection pooling + retry/backoff + per-host rate limit."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.s = requests.Session()
        retry = Retry(
            total=cfg.retries, connect=cfg.retries, read=cfg.retries,
            backoff_factor=1.0,                       # 1,2,4,8,16s
            status_forcelist=(429, 500, 502, 503, 504),
            respect_retry_after_header=True,
            allowed_methods=frozenset(["GET", "HEAD"]),
        )
        ad = HTTPAdapter(max_retries=retry, pool_connections=cfg.threads * 2,
                         pool_maxsize=cfg.threads * 4)
        self.s.mount("https://", ad)
        self.s.mount("http://", ad)
        self.s.headers.update({"User-Agent": f"journaldl (mailto:{cfg.email})"})

    def _throttle(self, url: str) -> None:
        host = re.sub(r"^https?://([^/]+).*", r"\1", url)
        with self._lock:
            wait = self._last.get(host, 0) + self.cfg.rate_limit - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def get(self, url: str, **kw) -> requests.Response:
        self._throttle(url)
        kw.setdefault("timeout", self.cfg.timeout)
        return self.s.get(url, **kw)

    def get_json(self, url: str, **kw) -> dict:
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()


# --------------------------------------------------------------------------- #
# SQLite state  (resume + idempotency)
# --------------------------------------------------------------------------- #
class State:
    """Per-run article ledger. Status: pending|done|failed|no_free_pdf|blocked|skipped."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.cx = sqlite3.connect(str(db_path), check_same_thread=False)
        self.cx.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self.cx.executescript(
            """
            CREATE TABLE IF NOT EXISTS article(
                key        TEXT PRIMARY KEY,   -- doi or openalex id
                doi        TEXT, oa_id TEXT, title TEXT,
                year INT, volume TEXT, issue TEXT, first_page TEXT, sort_key TEXT,
                pdf_url TEXT, landing TEXT, oa_status TEXT,
                status     TEXT DEFAULT 'pending',
                file       TEXT, source TEXT, error TEXT,
                attempts   INT DEFAULT 0, updated REAL
            );
            CREATE INDEX IF NOT EXISTS idx_status ON article(status);
            CREATE INDEX IF NOT EXISTS idx_issue  ON article(year,volume,issue);
            """
        )
        self.cx.commit()

    def upsert(self, a: dict) -> None:
        """Insert a discovered article; never clobbers an already-finished one."""
        with self._lock:
            self.cx.execute(
                """INSERT INTO article(key,doi,oa_id,title,year,volume,issue,
                       first_page,sort_key,pdf_url,landing,oa_status,updated)
                   VALUES(:key,:doi,:oa_id,:title,:year,:volume,:issue,
                       :first_page,:sort_key,:pdf_url,:landing,:oa_status,:t)
                   ON CONFLICT(key) DO UPDATE SET
                       pdf_url=COALESCE(excluded.pdf_url,article.pdf_url),
                       landing=COALESCE(excluded.landing,article.landing)
                   WHERE article.status='pending'""",
                {**a, "t": time.time()},
            )
            self.cx.commit()

    def pending(self) -> list[dict]:
        cur = self.cx.execute(
            "SELECT * FROM article WHERE status IN ('pending','failed') ORDER BY sort_key")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def mark(self, key: str, status: str, *, file: str = None,
             source: str = None, error: str = None) -> None:
        with self._lock:
            self.cx.execute(
                """UPDATE article SET status=?, file=?, source=?, error=?,
                       attempts=attempts+1, updated=? WHERE key=?""",
                (status, file, source, (error or "")[:500], time.time(), key))
            self.cx.commit()

    def issues(self) -> list[tuple]:
        return self.cx.execute(
            """SELECT year,volume,issue FROM article WHERE status='done'
               GROUP BY year,volume,issue ORDER BY year,volume,issue""").fetchall()

    def done_in_issue(self, y, v, i) -> list[dict]:
        cur = self.cx.execute(
            """SELECT * FROM article WHERE status='done'
               AND year IS ? AND volume IS ? AND issue IS ? ORDER BY sort_key""",
            (y, v, i))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self) -> dict[str, int]:
        return dict(self.cx.execute(
            "SELECT status,COUNT(*) FROM article GROUP BY status").fetchall())


# --------------------------------------------------------------------------- #
# OpenAlex: resolve journal + enumerate articles
# --------------------------------------------------------------------------- #
ISSN_RE = re.compile(r"\b(\d{4}-\d{3}[\dxX])\b")


class OpenAlex:
    def __init__(self, http: Http, cfg: Config):
        self.http, self.cfg = http, cfg

    def _params(self, **extra) -> dict:
        p = {"mailto": self.cfg.email, **extra}
        if self.cfg.api_key:
            p["api_key"] = self.cfg.api_key
        return p

    def resolve_source(self, target: str) -> dict:
        """Accept an ISSN, a journal URL, or a name -> OpenAlex source.

        For a URL we scrape the page once and try, in order: an ISSN (exact
        match), then the journal's own title/name (fuzzy search). This lets a
        non-technical user just paste the archive URL and have it resolve.
        """
        html = self._fetch_page(target) if target.startswith("http") else ""

        # 1. Best: an exact ISSN (from the arg itself or scraped off the page).
        issn = self._to_issn(target, html)
        if issn:
            j = self.http.get_json(f"{OPENALEX}/sources",
                                   params=self._params(filter=f"issn:{issn}"))
            if j.get("results"):
                return j["results"][0]

        # 2. Fuzzy fallback. Build candidate search terms in priority order:
        #    the journal title scraped from the page, then (last resort) a name
        #    derived from the arg. A bare domain like "kjpp.net" never matches,
        #    so prefer the real title whenever the page gave us one.
        candidates = []
        if html:
            candidates += self._page_titles(html)
        if not target.startswith("http"):
            candidates.append(target)                       # arg was a name
        candidates.append(re.sub(r"https?://|www\.|\.[a-z]{2,}(?=/|$)|/.*",
                                 " ", target).strip())       # mangled-URL guess
        for term in dict.fromkeys(c for c in candidates if c):  # dedupe, keep order
            j = self.http.get_json(f"{OPENALEX}/sources",
                                   params=self._params(search=term, per_page=1))
            if j.get("results"):
                hit = j["results"][0]
                log.warning("No ISSN found - matched journal %r by name (%r). "
                            "If that is wrong, pass the ISSN directly.",
                            hit.get("display_name"), term)
                return hit
        raise SystemExit(
            f"Could not resolve a journal for: {target!r}. "
            "Try passing the journal's ISSN instead (e.g. 2093-3827).")

    def _fetch_page(self, url: str) -> str:
        """Fetch a landing page like a real browser, failing FAST.

        This is only used to sniff an ISSN/title for resolution, so we use a
        plain one-shot request (no retry storm): many publisher sites actively
        drop bot connections, and we don't want to hang ~30s retrying before
        falling back. If it fails, the caller still tries other strategies.
        """
        try:
            r = requests.get(url, timeout=15, headers={
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })
            return r.text if r.ok else ""
        except Exception as e:
            log.warning("Couldn't read %s (the site may block automated access). "
                        "Falling back to a name search; pass the ISSN for reliable "
                        "results.", url)
            log.debug("page fetch error: %s", e)
            return ""

    def _to_issn(self, target: str, html: str = "") -> Optional[str]:
        m = ISSN_RE.search(target)
        if m:
            return m.group(1)
        # publisher pages expose the ISSN as a meta tag or as plain "ISSN ...." text
        for pat in (r'citation_issn"[^>]*content="([^"]+)"',
                    r'content="([^"]+)"[^>]*name="citation_issn"',
                    r'(?:e-?ISSN|ISSN)[^0-9]{0,8}(\d{4}-\d{3}[\dxX])',
                    ISSN_RE.pattern):
            mm = re.search(pat, html, re.I)
            if mm:
                return mm.group(1).strip()
        return None

    @staticmethod
    def _page_titles(html: str) -> list[str]:
        """Candidate journal names scraped from a page, best first."""
        out = []
        for pat in (r'name="citation_journal_title"[^>]*content="([^"]+)"',
                    r'content="([^"]+)"[^>]*name="citation_journal_title"',
                    r'property="og:site_name"[^>]*content="([^"]+)"',
                    r"<title[^>]*>([^<]+)</title>"):
            m = re.search(pat, html, re.I)
            if m:
                # drop trailing "- Archives", ": Home", site noise after a separator
                raw = _html.unescape(m.group(1).strip())
                t = re.split(r"\s*[|:\-–]\s*", raw)[0].strip()
                if len(t) >= 4:
                    out.append(t)
        return out

    def iter_articles(self, source_id: str) -> Iterator[dict]:
        """Yield normalized article records for a source, cursor-paginated."""
        flt = f"primary_location.source.id:{source_id},is_paratext:false"
        cursor, seen = "*", 0
        while cursor:
            j = self.http.get_json(
                f"{OPENALEX}/works",
                params=self._params(filter=flt, per_page=200, cursor=cursor))
            for w in j.get("results", []):
                rec = self._normalize(w)
                if rec is None:
                    continue
                if self.cfg.years and not (self.cfg.years[0] <= (rec["year"] or 0)
                                           <= self.cfg.years[1]):
                    continue
                yield rec
                seen += 1
                if self.cfg.limit and seen >= self.cfg.limit:
                    return
            cursor = j.get("meta", {}).get("next_cursor")

    @staticmethod
    def _normalize(w: dict) -> Optional[dict]:
        if w.get("type") not in KEEP_TYPES:
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
        # sort_key keeps articles in printed order within an issue
        sort_key = f"{year or 0:04d}|{str(vol or ''):>8}|{str(issue or ''):>8}|" \
                   f"{int(fp) if fp.isdigit() else 0:08d}|{w.get('publication_date','')}"
        return {
            "key": doi or oa_id, "doi": doi, "oa_id": oa_id, "title": title[:300],
            "year": year, "volume": vol, "issue": issue, "first_page": fp,
            "sort_key": sort_key, "oa_status": (w.get("open_access") or {}).get("oa_status"),
            "pdf_url": loc.get("pdf_url"),
            "landing": loc.get("landing_page_url") or (w.get("primary_location") or {}).get("landing_page_url"),
        }


# --------------------------------------------------------------------------- #
# PDF URL resolution chain  (each resolver yields 0+ candidate URLs)
# --------------------------------------------------------------------------- #
class Resolver:
    def __init__(self, http: Http, cfg: Config):
        self.http, self.cfg = http, cfg

    def candidates(self, a: dict) -> list[str]:
        urls: list[str] = []
        def add(u):
            if u and u not in urls:
                urls.append(u)

        add(a.get("pdf_url"))                       # 1. OpenAlex direct
        if a.get("doi"):
            add(self._unpaywall(a["doi"]))          # 2. Unpaywall OA copy
            urls += self._epmc(a["doi"])            # 3. Europe PMC / PMC copy
        add(self._scrape_meta(a.get("landing")))    # 4. landing-page citation_pdf_url
        return urls

    def _unpaywall(self, doi: str) -> Optional[str]:
        try:
            j = self.http.get_json(f"{UNPAYWALL}/{quote(doi)}",
                                   params={"email": self.cfg.email})
            best = j.get("best_oa_location") or {}
            if best.get("url_for_pdf"):
                return best["url_for_pdf"]
            for loc in (j.get("oa_locations") or []):
                if loc.get("url_for_pdf"):
                    return loc["url_for_pdf"]
        except Exception as e:
            log.debug("unpaywall %s: %s", doi, e)
        return None

    def _epmc(self, doi: str) -> list[str]:
        try:
            j = self.http.get_json(
                f"{EPMC}/search",
                params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"})
            hits = j.get("resultList", {}).get("result", [])
            if not hits:
                return []
            h = hits[0]
            pmcid = h.get("pmcid")
            if pmcid and h.get("isOpenAccess") == "Y":
                return [f"{EPMC}/{h.get('source','PMC')}/{h['id']}/fullTextPDF",
                        f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"]
        except Exception as e:
            log.debug("epmc %s: %s", doi, e)
        return []

    def _scrape_meta(self, landing: Optional[str]) -> Optional[str]:
        if not landing:
            return None
        try:
            html = self.http.get(landing, headers={"User-Agent": BROWSER_UA}).text
            m = re.search(r'citation_pdf_url"\s+content="([^"]+)"', html)
            return m.group(1) if m else None
        except Exception as e:
            log.debug("meta scrape %s: %s", landing, e)
        return None


# --------------------------------------------------------------------------- #
# Downloader  (atomic + validated, with optional browser fallback)
# --------------------------------------------------------------------------- #
class Downloader:
    def __init__(self, http: Http, cfg: Config, resolver: Resolver):
        self.http, self.cfg, self.resolver = http, cfg, resolver
        self._browser = None  # lazy Playwright

    def fetch(self, a: dict, dest: Path) -> tuple[str, Optional[str], Optional[str]]:
        """Return (status, file_path, source_url). Never raises."""
        if dest.exists() and self._valid(dest):       # skip existing good file
            return "done", str(dest), "cache"
        urls = self.resolver.candidates(a)
        if not urls:
            return "no_free_pdf", None, None
        blocked = False
        for url in urls:
            ok, why = self._try(url, dest)
            if ok:
                return "done", str(dest), url
            blocked = blocked or (why == "blocked")
        if self.cfg.browser:                           # last resort: real browser
            for url in urls:
                if self._browser_fetch(url, dest) and self._valid(dest):
                    return "done", str(dest), url + " (browser)"
        return ("blocked" if blocked else "no_free_pdf"), None, None

    def _try(self, url: str, dest: Path) -> tuple[bool, str]:
        part = dest.with_suffix(".part")
        try:
            r = self.http.get(url, headers={"User-Agent": BROWSER_UA,
                              "Accept": "application/pdf,*/*"},
                              stream=True, allow_redirects=True)
            if r.status_code in (401, 403):
                return False, "blocked"
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(part, "wb") as fh:
                for chunk in r.iter_content(65536):
                    fh.write(chunk)
            if not self._valid(part):                  # HTML bot-wall / truncated
                part.unlink(missing_ok=True)
                return False, "invalid"
            part.replace(dest)                          # atomic
            return True, "ok"
        except Exception as e:
            log.debug("dl %s: %s", url, e)
            Path(part).unlink(missing_ok=True)
            return False, "error"

    @staticmethod
    def _valid(p: Path) -> bool:
        """A file is a good PDF only if it has the magic header AND pypdf can read >=1 page."""
        try:
            if p.stat().st_size < 1024:
                return False
            with open(p, "rb") as fh:
                if fh.read(5) != b"%PDF-":
                    return False
            return len(PdfReader(str(p)).pages) >= 1
        except (PdfError, OSError, Exception):
            return False

    def _browser_fetch(self, url: str, dest: Path) -> bool:
        try:
            if self._browser is None:
                from playwright.sync_api import sync_playwright  # lazy import
                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(headless=True)
            ctx = self._browser.new_context(accept_downloads=True, user_agent=BROWSER_UA)
            page = ctx.new_page()
            try:
                with page.expect_download(timeout=self.cfg.timeout * 1000) as dl:
                    page.goto(url)
                dl.value.save_as(str(dest))
                return True
            except Exception:
                resp = page.goto(url)                  # some serve PDF inline
                if resp and "pdf" in (resp.headers.get("content-type", "")):
                    dest.write_bytes(resp.body())
                    return True
                return False
            finally:
                ctx.close()
        except Exception as e:
            log.warning("browser fetch failed (%s): %s", url, e)
            return False

    def close(self):
        if self._browser:
            try:
                self._browser.close(); self._pw.stop()
            except Exception:
                ...


# --------------------------------------------------------------------------- #
# Merge per issue
# --------------------------------------------------------------------------- #
def safe(s: str) -> str:
    return re.sub(r"[^\w.-]+", "_", str(s)).strip("_") or "NA"


def merge_issue(name: str, articles: list[dict], out_dir: Path) -> Optional[Path]:
    """Merge an issue's PDFs in printed order with one bookmark per article."""
    writer = PdfWriter()
    n = 0
    for a in articles:
        f = a.get("file")
        if not f or not Path(f).exists():
            continue
        try:
            reader = PdfReader(f)
            start = len(writer.pages)
            for pg in reader.pages:
                writer.add_page(pg)
            writer.add_outline_item(a["title"][:120], start)  # navigable TOC
            n += 1
        except Exception as e:
            log.warning("merge skip %s: %s", f, e)   # corrupt file never breaks merge
    if n == 0:
        return None
    a0 = articles[0]
    fn = f"{safe(name)}_Vol{safe(a0.get('volume'))}_Iss{safe(a0.get('issue'))}_{n}arts.pdf"
    out = out_dir / fn
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fh:
        writer.write(fh)
    return out


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
class Job:
    def __init__(self, target: str, cfg: Config):
        self.cfg = cfg
        self.http = Http(cfg)
        self.oa = OpenAlex(self.http, cfg)
        self.resolver = Resolver(self.http, cfg)
        self.dl = Downloader(self.http, cfg, self.resolver)
        self.target = target
        self._stop = threading.Event()

    def run(self) -> None:
        t0 = time.time()
        src = self.oa.resolve_source(self.target)
        jname = src.get("display_name", "Journal")
        sid = src["id"].split("/")[-1]
        jdir = self.cfg.out / safe(jname)
        st = State(jdir / "_state.sqlite")
        log.info("Journal: %s (%s) | OpenAlex %s | in_doaj=%s",
                 jname, src.get("issn_l"), sid, src.get("is_in_doaj"))

        # STEP 1 - enumerate (idempotent; safe to re-run)
        log.info("Enumerating articles via OpenAlex ...")
        for rec in tqdm(self.oa.iter_articles(sid), desc="enumerate", unit="art"):
            st.upsert(rec)

        todo = st.pending()
        log.info("%d articles to fetch (resuming; already-done are skipped)", len(todo))

        # STEP 2 - download in parallel; isolate every item
        bar = tqdm(total=len(todo), desc="download", unit="pdf")
        def work(a: dict):
            if self._stop.is_set():
                return
            try:
                dest = (jdir / safe(a.get("year")) / f"Vol_{safe(a.get('volume'))}"
                        / f"Issue_{safe(a.get('issue'))}" / "PDFs"
                        / f"{safe(a.get('first_page') or a['key'])}.pdf")
                status, file, source = self.dl.fetch(a, dest)
                st.mark(a["key"], status, file=file, source=source)
            except Exception as e:                    # absolute backstop
                st.mark(a["key"], "failed", error=repr(e))
            finally:
                bar.update(1)

        with cf.ThreadPoolExecutor(max_workers=self.cfg.threads) as ex:
            futs = [ex.submit(work, a) for a in todo]
            try:
                for _ in cf.as_completed(futs):
                    pass
            except KeyboardInterrupt:
                self._stop.set()
                log.warning("Interrupted - checkpointed; re-run to resume.")
        bar.close()
        self.dl.close()

        # STEP 3 - merge per issue
        merged = 0
        if self.cfg.merge:
            iss = st.issues()
            for (y, v, i) in tqdm(iss, desc="merge", unit="issue"):
                arts = st.done_in_issue(y, v, i)
                if merge_issue(jname, arts, jdir / safe(y) / f"Vol_{safe(v)}" / f"Issue_{safe(i)}"):
                    merged += 1

        # STEP 4 - report
        s = st.stats()
        dt = time.time() - t0
        print("\n" + "=" * 56)
        print(f"  Journal           : {jname}")
        print(f"  Articles found    : {sum(s.values())}")
        print(f"  Downloaded (done) : {s.get('done', 0)}")
        print(f"  No free PDF       : {s.get('no_free_pdf', 0)}")
        print(f"  Blocked (bot wall): {s.get('blocked', 0)}  "
              f"{'(retry with --browser)' if s.get('blocked') else ''}")
        print(f"  Failed            : {s.get('failed', 0)}")
        print(f"  Merged issues     : {merged}")
        print(f"  Output            : {jdir}")
        print(f"  Elapsed           : {dt:.1f}s")
        print("=" * 56)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Resilient, publisher-agnostic journal PDF downloader.")
    p.add_argument("target", help="journal archive URL, ISSN, or name")
    p.add_argument("--email", help="contact email (polite pool + Unpaywall) - USE A REAL ONE")
    p.add_argument("--api-key", help="free OpenAlex API key (100k calls/day)")
    p.add_argument("--out", help="output directory (default: Downloads)")
    p.add_argument("--threads", type=int, help="parallel downloads (default 6)")
    p.add_argument("--rate", type=float, dest="rate_limit", help="min sec between requests/host")
    p.add_argument("--years", help="year range filter, e.g. 2020-2024")
    p.add_argument("--limit", type=int, help="cap #articles (testing)")
    p.add_argument("--browser", action="store_true", help="use Playwright for bot-walled hosts")
    p.add_argument("--no-merge", action="store_true", help="skip per-issue merge")
    p.add_argument("--config", help="path to config.json")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler("download.log", encoding="utf-8")])

    years = None
    if args.years:
        lo, hi = re.split(r"[-:]", args.years)
        years = (int(lo), int(hi))

    # Auto-discover config.json (cwd first, then next to this script) so users
    # don't have to pass --config. An explicit --config always wins.
    config_path = args.config
    if not config_path:
        for cand in (Path("config.json"), Path(__file__).with_name("config.json")):
            if cand.exists():
                config_path = str(cand)
                log.info("Using config: %s", cand)
                break

    cfg = Config.load(
        config_path, email=args.email, api_key=args.api_key, out=args.out,
        threads=args.threads, rate_limit=args.rate_limit, years=years,
        limit=args.limit, browser=args.browser or None,
        merge=False if args.no_merge else None)

    if cfg.email in ("you@example.com", "YOUR_REAL_EMAIL@example.com"):
        log.warning("Set a REAL email (in config.json or --email): it unlocks the "
                    "polite pool and Unpaywall, and is the courteous thing to do.")
    if not cfg.api_key or cfg.api_key == "YOUR_FREE_OPENALEX_KEY":
        log.warning("No OpenAlex API key set - limited to ~100 calls/day. "
                    "Add a free key to config.json for 100,000/day.")
    Job(args.target, cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
