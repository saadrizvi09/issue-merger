# Codex task: robust IOP journal PDF downloader over direct JMI campus-IP (ONOS)

## Context

I have legitimate subscription access to IOP Publishing journals (iopscience.iop.org)
through my university's national subscription (India's ONOS / "One Nation One
Subscription"). When my machine's public egress IP is a registered JMI (Jamia Millia
Islamia) campus IP — e.g. when connected to JMI WiFi — `iopscience.iop.org` serves full
PDFs directly, **IP-authenticated, with no login form**. This is NOT piracy; it is my
institution's paid access. Do not use Sci-Hub / LibGen / any shadow library.

Build a reliable, resume-safe command-line tool that downloads **every article PDF of a
given IOP journal for a given year range**, driven by a DOI manifest, while surviving
IOP's Radware bot-manager, transient failures, and long unattended runs.

Target platform: **Windows 11, Python 3.11+**. Browser automation via **Playwright**
(sync or async — async preferred) driving a **non-headless Chrome over the DevTools
Protocol (CDP)**. Do not use headless mode (Radware fingerprints it).

## Why a real browser + in-page fetch (critical design constraint)

IOP sits behind **Radware Bot Manager**. A plain `requests`/`httpx`/`curl` GET of
`/article/<doi>/pdf` gets challenged (redirect to `validate.perfdrive.com` captcha) or
returns HTML instead of a PDF. What works reliably:

1. Launch a real, visible Chrome with a persistent user-data-dir and `--remote-debugging-port`.
2. Connect Playwright via `connect_over_cdp("http://127.0.0.1:<port>")`.
3. Navigate a page to an `iopscience.iop.org` origin first (so the document origin matches).
4. Fetch the PDF **from inside the page** with `page.evaluate()` running
   `fetch(url, {credentials:'include'})`, read the `ArrayBuffer`, base64 it back to Python.
   Because this runs in the browser context with its cookies/TLS/JA3 fingerprint, Radware
   treats it as a normal same-origin XHR.

Reference in-page fetch (return status + bot flag + head bytes so callers can branch):

```js
async (u) => {
  try {
    const r = await fetch(u, {credentials:'include', redirect:'follow',
                             headers:{'Accept':'application/pdf,*/*'}});
    const b = new Uint8Array(await r.arrayBuffer());
    let s = ''; const C = 0x8000;
    for (let i=0;i<b.length;i+=C) s += String.fromCharCode.apply(null, b.subarray(i, Math.min(i+C,b.length)));
    const head = String.fromCharCode.apply(null, b.subarray(0,5));
    const bot  = (r.url||'').includes('perfdrive')
              || String.fromCharCode.apply(null, b.subarray(0,300)).toLowerCase().includes('bot manager');
    return {status:r.status, len:b.length, head:head, bot:bot, b64:btoa(s)};
  } catch(e) { return {status:0, len:0, head:'', bot:false, b64:''}; }
}
```

A response is a valid PDF iff `head === "%PDF-"` and `len > 10000`.

## Radware anti-bot rules (do not violate — these are load-bearing)

- **Serial only:** concurrency = 1. Concurrent PDF fetches trip the bot manager almost
  immediately. One worker, one fetch at a time.
- **Pace it:** ~2.0–2.5 s delay between successful fetches.
- **Cool down on block:** if a fetch returns non-PDF with the `bot` flag, sleep ~45 s,
  then re-navigate the page to `https://iopscience.iop.org/` (a real navigation resets the
  rate window) before retrying. Cap at ~5 attempts per article.
- **Relaunch Chrome between batches:** kill and relaunch the debug Chrome between batches;
  a fresh browser process clears accumulated bot score. This is the single most effective
  recovery lever — a batch that stalls almost always recovers after a Chrome relaunch.
- **404 = skip immediately, no retries.** A 404 means the article PDF genuinely doesn't
  exist (retracted, or online-first with no typeset PDF yet). Retrying a 404 five times with
  cooldowns burns the whole batch budget and starves reachable articles. Detect `status===404`
  and skip on the first attempt.

## Egress-IP preflight (JMI NAT pool caveat)

JMI WiFi is behind a NAT pool with several public IPs; **only some are registered** with
IOP/ONOS. A WiFi reconnect can silently drop you onto an unregistered IP, in which case
every PDF fetch fails even though "you're on JMI WiFi." Before each run (and log it):

1. From inside the browser, fetch `https://api.ipify.org` to read the current egress IP.
2. Do a **preflight PDF fetch** of one known-good, definitely-published DOI. If it does not
   return `%PDF-`, print a loud `NO ACCESS on this IP (egress <ip>) — reconnect to JMI /
   toggle WiFi` and **abort the batch fast** (don't grind through the whole queue failing).
   Make the preflight DOI a CLI arg.

Direct-IP URLs (no proxy rewrite): article PDF = `https://iopscience.iop.org/article/<DOI>/pdf`.
No login page is ever expected on a registered IP — if you get redirected to a login/SSO or
an ONOS landing page, treat it like NO ACCESS and abort with a clear message.

## Inputs

### DOI manifest (build a companion script for this too)

`iop_<issn>_dois.json` shape:

```json
{
  "journal": "Journal of Physics: Condensed Matter",
  "issn": "0953-8984",
  "issues": {
    "<volume>-<issue>": [
      {"doi":"10.1088/1361-648x/....","year":2025,"volume":"37","issue":"12","page":"125001"}
    ]
  }
}
```

Build it from **Crossref** (`https://api.crossref.org/journals/<issn>/works`, cursor-paged,
filter `from-pub-date`/`until-pub-date`) and/or **OpenAlex**
(`https://api.openalex.org/works?filter=primary_location.source.issn:<issn>,...`). For each
work capture DOI, year, volume, issue, first page.

**Year-bucketing caveat (important, we got this wrong before):** place an article in a year
by its **issue's cover year** (Crossref `published-print` / volume-based), NOT by an
online-first publication date. Online-first dates drift articles into the wrong year and
create per-issue gaps. Audit completeness **by volume against each issue's table of
contents**, not by a naive year filter, or you'll silently miss volume-boundary issues.

## Outputs

- Download tree: `iop_<issn>_downloads/V<vol>I<iss>/<doi-with-slashes-replaced>.pdf`
  (sanitize DOI: replace every char not in `[A-Za-z0-9._-]` with `_`).
- Articles with no assigned volume/issue → bucket `V_I_/`.
- Skip files that already exist and are `> 10 KB` (this is the resume mechanism).

## CLI

```
python iop_download.py \
  --manifest iop_0953-8984_dois.json \
  --dldir    iop_0953-8984_downloads \
  --base     https://iopscience.iop.org \
  --cdp      http://127.0.0.1:9223 \
  --preflight-doi 10.1088/1361-648x/ab7f6e \
  --min-year 2023 --max-year 2026 \
  --conc 1 --delay 2.2 --budget 480
```

- `--budget` = seconds of wall-clock per invocation, then exit cleanly (so it fits under a
  supervisor's timeout and is trivially restartable). Start the budget timer at process
  launch, including warmup.
- Sort the work queue by `(year, volume, issue, doi)` and process in order, but see the
  404-skip rule so dead early items can't block later years.
- Always exit 0 on "did some work / nothing to do"; use a distinct exit code only for
  NO-ACCESS so a supervisor can react.

## Supervisor loop (separate script)

`run_loop.py` — runs batches until the manifest is fully satisfied:

1. Compute remaining = manifest articles in [min_year,max_year] without a valid local PDF.
   Stop when 0 (excluding known-404s — see below).
2. Ensure the debug Chrome is up on the port; if not, launch it
   (`chrome.exe --remote-debugging-port=<port> --user-data-dir=<persistent dir> <journal url>`).
3. Run one `iop_download.py` batch.
4. **Kill and relaunch Chrome** (Radware reset) between every batch.
5. Loop.

Robustness requirements for the supervisor (all things that bit us):

- **Run fully detached on Windows** so it survives the parent terminal closing: spawn with
  `creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW`, write the PID
  to a file, append stdout/stderr to a log file. Provide a `launch_detached.py` that no-ops
  if already running (check the PID with `OpenProcess`/`WaitForSingleObject`).
- **No terminal pop-ups:** every child `subprocess.run/Popen` (the batch, any helper) must
  pass `creationflags=CREATE_NO_WINDOW`. A detached parent has no console, so console
  children otherwise spawn their own visible windows.
- **Kill the whole Chrome tree, by profile, not by port.** Killing only the process
  listening on the debug port leaves orphaned renderer/GPU children; over dozens of batches
  they accumulate and exhaust the Windows commit charge / paging file (hard crash:
  `0x800705AF "paging file too small"`, and even `subprocess`/`uv_spawn` start failing).
  Kill every `chrome.exe` whose command line contains the debug user-data-dir path — this
  targets only our instance and never touches the user's other Chrome windows:
  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
    Where-Object { $_.CommandLine -like '*<profile-leaf-name>*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  ```
- **Memory guard (low-RAM machines):** optionally check commit charge before launching
  Chrome; if within a small margin of the limit, wait/retry rather than crash.

## Known-404 handling

Some articles legitimately have no PDF (retracted / editorial notes / online-first without
typeset PDF). Maintain a `known_404.json` set: when the batch skips a DOI on a 404, record
it; the supervisor's "remaining" calculation excludes known-404s so the loop can actually
terminate instead of spinning forever on unreachable items. Log the final count of skipped
404s so the miss is visible (never silently treat 404s as "done").

## Completeness audit (separate script)

`audit.py`: for each volume/issue in the manifest, compare local valid-PDF count to the
issue's expected article count (from the manifest / a fresh TOC pull). Print a per-issue
gap report. Do the audit **by volume**, not by year.

## Acceptance criteria

1. On a registered JMI IP, a cold run downloads a full year's articles to the right
   `V<vol>I<iss>/` folders, resumable after Ctrl-C with no re-downloads.
2. On an unregistered IP, it aborts within ~one preflight with a clear NO-ACCESS message and
   the egress IP printed.
3. A 404 article is skipped in one attempt (visible in logs), and the run reaches later
   years instead of stalling.
4. Running the supervisor for hours does not leak Chrome processes or pop terminal windows,
   and does not crash from paging-file exhaustion.
5. Re-running after completion is a fast no-op (everything already present).
6. All access is via the real IP-authenticated browser session; no credentials, no proxies,
   no shadow libraries.

## Nice-to-haves

- `--journal-url` arg so the same tool works for any IOP journal by ISSN.
- Structured per-article logging (doi, status, bytes, attempt count, egress IP).
- A tiny `test_access.py` that fetches 2–3 sample DOIs through the live CDP Chrome and prints
  status + content-type, for quick "is my IP good right now?" checks.
```
```

Deliver: `build_manifest.py`, `iop_download.py`, `run_loop.py`, `launch_detached.py`,
`audit.py`, `test_access.py`, and a short README with the exact run commands.
