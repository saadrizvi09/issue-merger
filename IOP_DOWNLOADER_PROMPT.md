# CODEX TASK — Build a robust IOPscience journal PDF downloader (JMI/ONOS access)

## GOAL
Implement a resumable Python downloader that fetches **full-text article PDFs** for a
given IOP journal (iopscience.iop.org) across a year range, using my **legitimate
institutional access** (Jamia Millia Islamia campus Wi-Fi IP, recognised by IOP via
India's ONOS subscription). Output: one folder per issue with the article PDFs, ready
for a downstream merge step. This is authorised subscription access — do NOT use Sci-Hub,
LibGen, or any pirate source, and do NOT try to bypass/evade IOP's bot protection; just
ride the browser session that already has legitimate access.

## ENVIRONMENT
- Windows 11, Python 3.13. Allowed libs: `playwright`, `requests`, `PyMuPDF (fitz)`,
  standard lib. (`pip install playwright && playwright install chromium` if needed, but
  prefer connecting to my already-open Chrome — see ACCESS.)
- I run the script from `C:\Projects\Automate pdf merge journal`.
- Downloads go to `iop_<slug>_downloads/V<vol>I<issue>/<safeDOI>.pdf`
  where `safeDOI = doi.replace('/','_')` and slug is a short journal tag (e.g. `cm`, `cqg`).

## ACCESS (critical — IOP is bot-protected)
IOPscience sits behind **Radware bot mitigation**. Plain `requests`/`urllib`/`curl` get
challenged or return HTML challenge pages, NOT the PDF — even with valid institutional IP.
**You must drive a real, non-headless browser** that has already passed the challenge.

Two supported access paths (make it configurable via CLI `--access ip|proxy`):
1. **`ip`** (on JMI campus Wi-Fi): the campus public IP is registered with IOP/ONOS, so
   `https://iopscience.iop.org/...` resolves to full text directly.
2. **`proxy`** (off-campus, JMI MapMyAccess EZproxy): rewrite the host by replacing dots
   with dashes and appending the proxy domain, e.g.
   `iopscience.iop.org` → `iopscience-iop-org.jmi.mapmyaccess.com`
   (requires an interactive JMI login in the browser first). Note the proxy IP is shared
   and throttles harder — keep concurrency low.

**Browser driving (proven pattern):**
- I will launch Chrome myself with:
  `chrome.exe --remote-debugging-port=9222 --user-data-dir=<profile> --start-minimized`
  and log in / clear any Radware "checking your browser" interstitial once, manually.
- Your script connects via Playwright **`connect_over_cdp("http://127.0.0.1:9222")`**,
  reuses the existing context (shares cookies + bot clearance + institutional IP), and
  does all fetching inside that page/context.
- Warm-up: navigate to the journal home once, wait until `document.title` no longer
  contains "just a moment" / "checking" before starting.
- Do NOT launch headless Chrome — Radware flags it.

## PDF FETCH MECHANISM
For each article DOI, the PDF endpoint is:
  `https://iopscience.iop.org/article/<DOI>/pdf`
(under proxy: the rewritten host). Fetch by **navigating a browser tab** to that URL and
capturing the resulting download/response, NOT by `requests.get`. Two options, implement
whichever is reliable:
  a) `page.goto(pdf_url)` inside `page.expect_download()` — IOP serves the PDF as a
     download; save it and verify.
  b) If it renders inline, use `context.request.get(pdf_url)` (Playwright's request API
     runs *inside* the authenticated browser context, so it carries the bot clearance)
     and write `response.body()`.
**Always validate**: the saved bytes must start with `%PDF-` and be > 10 KB. If you get an
HTML page (challenge / "Access through your institution" / login), treat as FAILURE, back
off, and retry later — never save it as a PDF.

## ENUMERATION (which articles to download)
Build the work-list per **issue**, and bucket by **volume/issue**, NOT by print year.
> Gotcha we hit before: filtering Crossref by publication YEAR silently drops
> volume-boundary articles (published online in an adjacent year), causing per-issue gaps.
> Enumerate by volume→issue and validate counts against the issue TOC.

Steps:
1. Inputs (CLI): `--issn`, `--slug`, `--vol-year "38:2021,39:2022,..."` (volume→year map),
   optionally `--issues-per-vol` or discover from TOC.
2. Get the DOI list per issue. Preferred source = **IOP issue TOC pages**
   (`https://iopscience.iop.org/issue/<ISSN-nohyphen-or-journalcode>/<vol>/<issue>`),
   scraped inside the browser (they list every article DOI). Fallback = **Crossref**
   `https://api.crossref.org/journals/<ISSN>/works?filter=from-pub-date:<y-1>-06-01,until-pub-date:<y+1>-06-30&rows=1000&select=DOI,volume,issue,title,page`
   then group by `(volume, issue)` using a WIDE date window so boundary articles aren't lost.
3. Write the manifest to `iop_<ISSN>_dois.json`:
   `{"issn","journal","total_articles", "issues": {"V<vol>I<iss>": [{"doi","title","page","year","volume","issue"}, ...]}}`
   (`page` may be an IOP article-number like "015001" — keep it; downstream orders by it.)

## CONCURRENCY, RATE-LIMITING, RESUME
- IOP is the most bot-limited publisher we deal with. Use **low concurrency: 2–4 tabs max**
  in the SAME browser context. `proxy` mode: use 2.
- Randomised polite delay (0.5–2 s) between requests; exponential backoff (2,4,8,16 s) on
  any failure/challenge; after N consecutive challenges, pause 60 s and re-warm-up.
- **Resume-safe**: before fetching, skip any `<safeDOI>.pdf` that already exists AND starts
  with `%PDF-`. Delete & re-fetch partial/HTML files.
- Long runs die if backgrounded on this machine — design for foreground, resumable,
  re-run-until-complete. Persist progress to `iop_<slug>_progress.json` (per-DOI status)
  so re-running only fetches what's missing.
- If IOP starts hard-blocking (repeated challenges / the proxy IP gets suspended under
  load), STOP cleanly and report how far you got — do not hammer.

## FILE LAYOUT & VALIDATION
- Save to `iop_<slug>_downloads/V<vol>I<iss>/<safeDOI>.pdf`.
- After each issue, log `got/expected` article count.
- At the end, print a per-year/volume audit: for each issue, downloaded-valid vs
  manifest-expected, and total valid PDFs + total pages (via `fitz` page_count).

## CLI / DELIVERABLES
Provide `iop_download.py` with:
```
python iop_download.py --issn 0953-8984 --slug cm \
    --vol-year "31:2019,32:2020,33:2021,34:2022" \
    --access ip --cdp http://127.0.0.1:9222 --conc 3
```
- `enumerate` subcommand (build/refresh the manifest) and `download` subcommand
  (resumable fetch), or a `--enumerate-only` flag.
- Clear stdout progress; structured `progress.json`; graceful Ctrl-C (finish in-flight,
  save state).

## ACCEPTANCE CRITERIA
1. On JMI Wi-Fi (`--access ip`) with my Chrome open on port 9222, it downloads real,
   openable PDFs (every file starts with `%PDF-`, > 10 KB, opens in fitz).
2. Re-running does zero redundant work (resume) and converges to 100% of the manifest.
3. Per-issue counts match the issue TOC (no boundary-year gaps).
4. It never saves challenge/login HTML as a PDF, and it backs off instead of hammering
   when throttled.
5. No pirate sources, no headless-evasion tricks — purely the authenticated browser session.

## NOTES / KNOWN VALUES
- Example IOP journals & DOI prefixes: J. Phys.: Condensed Matter (ISSN 0953-8984, DOI
  `10.1088/1361-648x/...`), Classical & Quantum Gravity (0264-9381, `10.1088/1361-6382/...`),
  JCAP (1475-7516, `10.1088/1475-7516/<year>/<month>/<art>` — for JCAP volume == year and
  "issue" == month). Condensed Matter vol↔year: v30=2018 … v38=2026.
- Some articles have no assigned issue yet (online-first) — Crossref/TOC will show them
  under a placeholder; keep them under a `V?I?`-style bucket but DON'T block issue merges on them.
```
