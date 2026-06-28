# journaldl — resilient, publisher-agnostic journal PDF downloader

One file. Give it a journal **ISSN** (preferred), URL, or name; it discovers every
article via OpenAlex, downloads the open-access PDFs, and merges one PDF per issue.

## Easiest way (no commands, Windows)
Double-click **`run.bat`**. It installs what it needs, then asks you to paste
either the journal's **ISSN** *or* its **archive URL** — that's it. Output lands
in the `Downloads` folder next to the script. Re-run it any time to resume.

## Install
```bash
pip install -r requirements.txt   # or: pip install requests pypdf tqdm
# optional, only for --browser (defeats Karger/Elsevier bot walls):
pip install playwright && playwright install chromium
```

## Get a free OpenAlex key (needed for big jobs)
Since Feb 2025 OpenAlex needs a key: 100 calls/day without, **100,000/day with a free key**.
Grab one at https://openalex.org → put it in `config.json` or pass `--api-key`.
Always set a **real `--email`** (Unpaywall requires it; it unlocks the OpenAlex polite pool).

## Run
```bash
# preferred: pass the ISSN directly (reliable resolution)
python journaldl.py 2671-826X --email you@x.com --api-key KEY

# URL works too: it scrapes the page for the ISSN, and if that is hidden it
# searches OpenAlex by the journal's title (read off the page) before guessing
python journaldl.py "https://www.kjpp.net/journal/archives.html"

# filter years, cap for a test, tune concurrency
python journaldl.py 2296-9357 --email you@x.com --years 2020-2024 --limit 20 --threads 8

# bot-walled publishers (Karger/Elsevier): add the browser path
python journaldl.py 2296-9357 --email you@x.com --browser
```

## Output
```
Downloads/<Journal>/<Year>/Vol_X/Issue_Y/PDFs/*.pdf      individual articles
Downloads/<Journal>/<Year>/Vol_X/Issue_Y/<Journal>_VolX_IssY_Narts.pdf   merged, bookmarked
Downloads/<Journal>/_state.sqlite                         resume ledger
download.log                                              full log
```

## Resilience ("never fails" = never crashes, never loses work)
- Each article runs in isolation; one bad item never aborts the run.
- Progress is in SQLite — **re-run to resume**; only pending/failed items retry. Ctrl-C is safe.
- Downloads are atomic (`.part`→rename) and validated (PDF magic + pypdf parse), so an
  HTML "bot wall" page or a truncated file is **never** mistaken for a real PDF.

## What it can and cannot get
It downloads whatever has a **free, downloadable** copy. It does **not** bypass paywalls
or invent files. Per-article status is recorded: `done`, `no_free_pdf`, `blocked` (bot
wall — try `--browser`), `failed`. Paywalled content stays unobtainable by design.
