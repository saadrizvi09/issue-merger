# JOURNAL PDF DOWNLOAD → MERGE → CLEAN → UPLOAD — STANDING WORKFLOW

Paste this at the start of a new session, then name the journal link(s) to process.

I download open-access academic journals, merge them per issue, clean them, and
upload to my Google Drive. Follow these rules exactly.

## SCOPE
- Years: 2018–2026 ONLY. Never collect pre-2018 or post-2026 unless I say so.
- I will give you one or more journal links (Springer / MSP / AIMS / Wiley / T&F /
  World Scientific / etc.). Process only the journals I name.

## ENVIRONMENT (already set up on this machine)
- Working dir: C:\Projects\Automate pdf merge journal
- Use the session scratchpad for all temp files (PDFs, scripts, merged output).
- Python 3.13 with: pypdf, PyMuPDF (fitz), playwright.
- Cleaner tool: C:/Projects/Automate pdf merge journal/pdf_clean.py
    usage: python pdf_clean.py SRC.pdf -o OUT.pdf
    (recolors links black, strips first-page URLs, removes open-access/green marks,
     KEEPS copyright, emails, logo. Do NOT pass --strip-emails.)
- rclone remote: gdrive:  (rclone.exe under the WinGet Rclone package path)
- Drive destination parent folder ID: 1MUEpDOX2FEjuHb3arXvrQWDKexHxNyYL
    upload with:  rclone copy/sync LOCAL "gdrive:<Journal Name>" --drive-root-folder-id 1MUEpDOX2FEjuHb3arXvrQWDKexHxNyYL

## STEP 0 — VERIFY DOWNLOADABILITY FIRST (CRITICAL — do not skip)
NEVER trust OpenAlex oa_status / "diamond" labels or the presence of a "Full Text"
link. They are frequently WRONG (they falsely mark paywalled AIMS/MSP journals as free).
The ONLY valid test:
  1. Take a real article per year (2018,2020,2022,2024,2025).
  2. Actually fetch/open the full text or PDF.
  3. Confirm the bytes start with %PDF- AND the page does NOT contain
     "Access restricted", "purchase or subscription", "login or make a payment".
Report, per year, whether it is genuinely free. A journal is "100% downloadable"
only if EVERY year passes. Tell me the honest coverage before downloading anything.
Watch for MOVING WALLS (e.g., MSP: only issues older than ~5 yrs are free).

## STEP 1 — ENUMERATE
- Determine the volume↔year mapping and the issues/articles per issue.
- MSP journals (msp.org): PDFs are direct URLs:
    https://msp.org/<slug>/<year>/<vol>-<issue>/<slug>-v<vol>-n<issue>-p<NN>-s.pdf
    Validate Content-Type is application/pdf (a 200 with HTML = paywalled, not a PDF).
- JS-gated sites (AIMS, Wiley, T&F, Springer): use a real logged-in browser via CDP
  (see STEP 4). Get article DOIs/IDs from the publisher or OpenAlex/Crossref.
- Use the ISSUE year (from the publisher URL or Crossref journal-issue), NOT the
  online-first / OpenAlex publication_year (which drifts issues into the wrong year).

## STEP 2 — DOWNLOAD (resume-safe)
- Save real PDFs only (check %PDF- header, size > 1 KB). Skip already-downloaded files.
- Be gentle: MSP and others rate-limit bursts. Use 3–6 workers + exponential-backoff
  retry. Long jobs die if backgrounded on this machine — run FOREGROUND, resume-safe,
  re-run until complete (set a long tool timeout).

## STEP 3 — MERGE + CLEAN (per issue)
- PREFER a publisher full-issue PDF if one exists — it's cleaner (front matter, TOC,
  correct pagination) and no manual merge needed. For MSP the full issue is the same
  URL as an article minus the -p<NN> part:  <slug>-v<vol>-n<issue>-s.pdf
  (e.g. mt-v1-n1-s.pdf). Always probe this BEFORE downloading individual articles.
  Note: the newest in-progress issue may have no full-issue PDF yet (404) — then fall
  back to the individual article(s) for that issue only.
- ONLY if there is no full-issue PDF (e.g. Wiley/Elsevier/T&F): download individual
  article PDFs (page order p01,p02,…) and merge them into ONE PDF per issue.
- Run pdf_clean.py on each issue PDF.


## STEP 5 — NAMING & FOLDER STRUCTURE (exact)
- File name:  <ISSN-without-hyphen>V<vol>I<issue>.pdf
    e.g. ISSN 2832-6903, vol 3, issue 2  ->  28326903V3I2.pdf
- Drive layout:  <Journal Name> / <year> / <ISSNnohyphenVxIx>.pdf
    (one folder per journal, then a subfolder per year)

## STEP 6 — UPLOAD
- Pre-create the journal folder, then upload the year-tree with rclone into the
  parent folder ID above. Verify with "rclone lsf -R". Avoid parallel folder
  creation (it makes duplicate same-name Drive folders).
- Only upload when I say to (sometimes I want download + page count first).

## ALWAYS REPORT
- Per journal: articles, issues, total pages, and honest free-coverage %.
- If not 100% free, tell me exactly which years/issues are missing and why
  (paywall / moving wall / journal didn't exist yet). Never silently skip.
- Never remove or conceal "RETRACTED" watermarks.

Start by telling me which journal link(s) you want done; I'll verify downloadability
(STEP 0) first and report before downloading.
