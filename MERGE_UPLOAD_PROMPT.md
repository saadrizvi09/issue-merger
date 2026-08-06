# CODEX TASK — Journal PDF merge → upload-to-Drive → manifest → cleanup pipeline

## GOAL
Given a folder of **already-downloaded article PDFs** for an academic journal (plus a DOI
manifest JSON), build a resumable Python pipeline that:
1. audits per-issue completeness,
2. merges each **complete** issue into one PDF (page/article ordered),
3. uploads to my Google Drive under `<Journal>/<year>/`,
4. builds an issue-level `<ISSN> MANIFEST.xlsx` (keywords from OpenAlex) and uploads it,
5. deletes local copies that are safely on Drive (preserving anything NOT uploaded).

Everything below is distilled from a working pipeline across ~16 journals — follow the
conventions and honor every "GOTCHA".

## ENVIRONMENT
- Windows 11, Python 3.13. Libs: `PyMuPDF (fitz)`, `openpyxl`, standard lib, `urllib`.
- Work dir: `C:\Projects\Automate pdf merge journal`.
- rclone: `C:\Users\acer\AppData\Local\Microsoft\WinGet\Packages\Rclone.Rclone_*\rclone-*\rclone.exe`
  remote `gdrive:`, Drive collection **parent folder ID = 1MUEpDOX2FEjuHb3arXvrQWDKexHxNyYL**.
  Upload with `--drive-root-folder-id <that id>`.
- Long jobs can die if backgrounded on this machine → run FOREGROUND, resumable, re-runnable.

## INPUTS (CLI)
- `--store` : download dir(s), e.g. `iop_cm_downloads` (allow MULTIPLE; union them — GOTCHA A).
- `--manifest` : DOI JSON. Two shapes supported:
  - `{"issn","journal","total_articles","issues": {"V<vol>I<iss>": [ {doi,title,page,year,volume,issue}, ... ]}}`
  - Springer-style `{"journals": {"<id>": {name,issn,total,issues:{...}}}}`
- `--issn`, `--journal-name` (Drive folder + BOOK TITLE), `--slug`.
- `--vol-year "30:2018,31:2019,..."` OPTIONAL explicit map; otherwise bucket each issue by
  the **modal `year`** of its articles (GOTCHA B).
- `--year-range 2018-2026` (default; never collect outside unless told).

## STEP 1 — AUDIT (per issue, honest)
- Build a **global lookup**: `safeDOI(lower) -> filepath` over ALL `--store` dirs
  (`safeDOI = doi.replace('/','_')`), keeping only files whose first 5 bytes == `%PDF-`.
- For each manifest issue key, `got = # of its DOIs present in lookup`; issue is COMPLETE iff
  `got == len(arts)` and `len>0`.
- Bucket issues to a **year** (from `--vol-year` or modal article year); ignore out-of-range.
- Print per-year: complete-issues / total-issues and got/expected articles. This is the
  honest coverage report — never silently skip; show exactly what's incomplete.

## STEP 2 — MERGE (complete issues only)
- For each COMPLETE issue, order articles by **first page number** (`page` like "1061-1076"
  → 1061). If the manifest has NO page ranges (IOP: `page` is an article id like "037"/"015001"),
  order by that article number, else by DOI (GOTCHA C).
- Merge with fitz:
  ```python
  out = fitz.open()
  for p in paths:
      s = fitz.open(p); out.insert_pdf(s); s.close()
  out.save(outp, garbage=0)   # GOTCHA D: stream to disk; do NOT use out.tobytes()
  out.close()
  ```
- Output `OUTDIR/<year>/<ISSN-nohyphen>V<vol>I<iss>.pdf`. Keep the issue token verbatim so
  combined issues `V291I1-2` and supplement issues `V326IS1` are preserved (GOTCHA E).
- **SKIP** any issue key containing `?` (e.g. `V?I?`) — these are online-first articles with
  no assigned issue; they can't be placed and `?` is an illegal Windows filename char.
  Count & report them; do NOT block on them (GOTCHA F).
- Resume-safe: if `outp` exists and opens in fitz with pages, skip re-merge.

## STEP 3 — NAMING & FOLDER (exact)
- File: `<ISSN-without-hyphen>V<vol>I<issue>.pdf` (e.g. `00949655V88I1.pdf`).
- Drive layout: `<Journal Name>/<year>/<file>` inside the parent folder ID.

## STEP 4 — UPLOAD (rclone, sequential)
- `rclone copy OUTDIR "gdrive:<Journal Name>" --drive-root-folder-id <ID> --transfers 4
   --checkers 8 --drive-chunk-size 64M -P`.
- Do ONE rclone process at a time (GOTCHA G: parallel rclone creates duplicate same-name
  Drive folders; if that happens, `rclone dedupe --dedupe-mode skip`).
- Verify: `rclone lsf "gdrive:<Journal>/<year>"` count == local count, per year. Also confirm
  no duplicate journal folder in the parent.
- If uploading into an existing journal folder, only push the NEW year subfolders.

## STEP 5 — MANIFEST `<ISSN> MANIFEST.xlsx` (issue-level, one per journal)
Sheet1, header row (KEEP the exact odd spacing):
`Date ` | `Vendor_Name ` | `ISSN_Volume_Issue` | `BOOK TITLE` | `LANGUAGE` | `BUCKET` |
` Jrnl _Vol` | `Jrnl_Issue` | `Jrnl_Year` | `KEYWORD/ TOPIC` | `KEYWORD/TOPIC` |
`PRICE PER  ISSUE ` | `COMMENT` | `NOS OF ARTICLE` | `  PAGES`

One row per uploaded issue:
- Date = today `DD.MM.YYYY`; Vendor_Name/LANGUAGE/BUCKET/PRICE/COMMENT = blank.
- ISSN_Volume_Issue = the PDF basename (e.g. `00949655V88I1`); BOOK TITLE = journal UPPERCASE.
- Jrnl_Vol int, Jrnl_Issue (int if numeric else string e.g. "1-2","S1"), Jrnl_Year int.
- NOS OF ARTICLE = article count; PAGES = **actual merged-PDF page count** via fitz.
- KEYWORD/TOPIC (2 cols): aggregate each issue's article keywords, dedupe, split ~half into
  each column. Fetch from **OpenAlex**, batching ≤50 DOIs/request, cache to a json (resumable):
  `https://api.openalex.org/works?filter=doi:D1|D2|...&per_page=50&select=doi,keywords,concepts&mailto=joydip@bajarangs.com`
  use `keywords[].display_name`; fallback to `concepts` (level>=1). ~99% hit rate.
  (GOTCHA H: `json.dump` escapes unicode; en-dash/accents show as `?` only in Git Bash —
  the .xlsx itself is correct; verify with `cell.encode('unicode_escape')` if unsure.)
- Column widths ~ `[11,13,20,42,10,8,10,10,10,45,45,12,10,14,9]`, bold+wrap header.
- Upload the xlsx into the same `gdrive:<Journal Name>` folder.
- To UPDATE an existing manifest (new issues added): load it, append new rows, re-sort by
  (year, vol, issue), save, re-upload.

## STEP 6 — CLEANUP (delete only what's on Drive)
- After verifying Drive counts, delete the merged output dir and the download files whose
  issues are now uploaded.
- **PRESERVE** anything NOT on Drive: incomplete issues, the `V?I?`/online-first folder, and
  any journal/year not uploaded. Never delete the last local copy of un-uploaded content.
- Report GBs freed and exactly what was kept and why.

## GOTCHAS (all learned the hard way — do not skip)
- **A. Two-store union**: an article's file may live in a different store/folder than its
  manifest key implies → always resolve via the global `safeDOI->path` lookup, not by
  assuming `store/<key>/<safeDOI>.pdf`.
- **B. Year bucketing**: many journals have several volumes/year → bucket by modal article
  year, not vol==year. And use ISSUE/print year, never OpenAlex online-first year, or issues
  drift into the wrong year folder.
- **C. Ordering**: page-range journals → first-page order; IOP-type (no page ranges) →
  article-number order (`page` field) or DOI.
- **D. fitz save**: `out.tobytes()` OOMs on big issues; use `save(garbage=0)` streaming.
- **E. Combined/supplement issues**: keys like `V291I1-2`, `V326IS1` are REAL — don't drop
  them with a strict `V\d+I\d+` regex; only exclude keys containing `?`.
- **F. Online-first `V?I?`**: exclude from merge (illegal filename + not a real issue),
  count + preserve locally.
- **G. rclone dup folders**: sequential uploads only; pre-create/verify folder.
- **H. Manifest unicode** display artifact in Git Bash only.
- **I. Manifest changed?** If the source manifest was re-enumerated (more articles in an
  existing issue), do a FULL re-merge for that journal — resume-safe skip would keep stale
  PDFs missing the new articles.
- **J. Boundary issues**: a single-year download bucketed by print year misses volume-boundary
  articles (published adjacent year). Audit per VOLUME against issue TOCs, not per year.

## DELIVERABLES
`journal_pipeline.py` with subcommands `audit | merge | upload | manifest | cleanup` (and an
`all` that runs them in order), driven by the CLI inputs above; structured progress logs;
graceful resume on re-run.

## ACCEPTANCE CRITERIA
1. Only 100%-complete issues get merged/uploaded; incomplete + `V?I?` are reported and kept.
2. Every merged PDF opens in fitz; Drive per-year counts match local; no duplicate folders.
3. `<ISSN> MANIFEST.xlsx` matches the exact column format, one row per uploaded issue, with
   real page counts and OpenAlex keywords, and lands in the journal's Drive folder.
4. Cleanup deletes only Drive-backed files and never the sole copy of un-uploaded content.
5. Re-running is idempotent (resume). Honest coverage reported throughout; nothing silently skipped.
