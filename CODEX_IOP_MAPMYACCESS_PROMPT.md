# Codex task: robust IOP journal PDF downloader via MapMyAccess reverse proxy (works from home IP)

## Context

My university (JMI) provides an EZProxy-style **reverse proxy called MapMyAccess** that
grants my licensed IOP access **from any IP, including my home connection** (unlike direct
campus-IP access, which only works on JMI WiFi). Access is authenticated by **logging in**
through the proxy (OAuth → Microsoft/institutional SSO), not by IP. This is my institution's
legitimate paid subscription — not piracy. Never use Sci-Hub / LibGen / any shadow library.

Build a reliable, resume-safe CLI that downloads **every article PDF of a given IOP journal
for a year range**, driven by a DOI manifest, routed through MapMyAccess, surviving the
proxy's login-session expiry, IOP's Radware bot-manager, shared-proxy throttling, and long
unattended runs.

Target: **Windows 11, Python 3.11+**, **Playwright** driving a **non-headless Chrome over
CDP** with a **persistent user-data-dir** (the persistent profile is what keeps the SSO
session alive between runs). Do not use headless (Radware fingerprints it, and the SSO flow
needs a real profile).

## The MapMyAccess model (this is the whole difference vs direct-IP)

### 1. URL rewrite

Every publisher host is reached through a rewritten proxy hostname: replace `.` with `-`
in the original host and append `.jmi.mapmyaccess.com`.

- `iopscience.iop.org`  ->  `iopscience-iop-org.jmi.mapmyaccess.com`
- Article PDF: `https://iopscience-iop-org.jmi.mapmyaccess.com/article/<DOI>/pdf`
- Journal page: `https://iopscience-iop-org.jmi.mapmyaccess.com/journal/<issn>`

Make the proxy base URL a CLI arg (`--base https://iopscience-iop-org.jmi.mapmyaccess.com`)
so the same tool works for other publishers by changing the host.

### 2. Login flow (the fiddly part — implement exactly)

When the proxy session is absent/expired, navigating to any proxied URL **redirects to the
login page**:

- URL: `https://jmi.mapmyaccess.com/login?redirect=<encoded original proxied url>`
- Page title: `Login | MMA | Jamia Millia Islamia`

On that login page there is an institutional-login button:

- Text is shown as "Student Login" / "OAuth"
- Markup: `<button class="social-button login-option-btn loginAuthBtnHover"
  onclick="handleLoginOption('OAuth:<org-id>')">`
- **The button is present in the DOM but reports `not visible`, so Playwright's `.click()`
  times out.** You MUST trigger it with an in-page JS click:

  ```js
  () => {
    const btns = Array.from(document.querySelectorAll('button'));
    const b = btns.find(x => (x.getAttribute('onclick')||'').includes('OAuth'));
    if (b) { b.click(); return b.textContent.trim().slice(0,40); }
    return null;
  }
  ```

  (Try a normal `.click()` on `button.loginAuthBtnHover` and
  `button[onclick*="OAuth:"]` first for robustness, then fall back to the JS click — the JS
  click is the one that actually works.)

- Clicking OAuth kicks off **Microsoft / institutional SSO**. If the persistent Chrome
  profile already has a valid Microsoft session (see one-time seeding below), SSO
  **auto-completes with no typing** and redirects back to the proxied journal page.
- Consider login complete when the page title/URL returns to the IOP journal (e.g. title
  contains `IOPscience` / `Condensed Matter` / `Journal`, host back on
  `iopscience-iop-org.jmi.mapmyaccess.com`). Wait/poll up to ~60–90 s for the redirect chain.

Write this as a standalone `mma_login.py` that connects to the live CDP Chrome, performs the
above, and exits 0 on success / non-zero on failure, so the supervisor can call it.

### One-time manual seeding (document in README)

The very first time, the user logs in **manually once** in the persistent-profile Chrome
(institutional Microsoft account, incl. any MFA). After that the profile cookies keep the
Microsoft session, so `mma_login.py`'s OAuth click auto-completes headless-of-typing for
weeks. Do NOT hardcode credentials; rely on the seeded profile + SSO.

### 3. Session expiry — the failure mode that WILL bite you

The MapMyAccess session expires **server-side after ~45 minutes**. Critically:

> The journal **listing page keeps loading fine** after expiry, while **PDF fetches silently
> redirect to `jmi.mapmyaccess.com/login`**. So checking a visible tab's title for a login
> page does NOT reliably detect expiry — a batch can look healthy while downloading nothing
> for an hour.

Therefore:

- **Proactively re-login on a timer** (every ~25 min, safely under the ~45 min expiry),
  regardless of what any tab shows. Track `last_login` wall-clock time; before a batch, if
  `now - last_login > 25min`, run the login flow and reset the timer.
- **Also** re-login if a login page is detected.
- **And** make the batch itself treat a PDF fetch that lands on a `mapmyaccess.com/login`
  URL (or returns HTML instead of `%PDF-` with a login redirect) as "session dead → stop and
  signal re-login", not as a per-article failure.

## Anti-bot + shared-proxy throttling (Radware still applies through the proxy)

IOP behind the proxy is still fronted by **Radware Bot Manager**, AND the proxy IP is
**shared by many users**, so it throttles harder than a campus IP. Rules:

- **Serial only** (concurrency = 1). Concurrent fetches trip Radware immediately.
- **Pace:** ~2.0–2.5 s between successful fetches.
- **Bot-block cooldown:** if a fetch returns non-PDF with the `perfdrive`/"bot manager"
  signature, sleep ~45 s, re-navigate the page to the proxied journal homepage (real nav
  resets the rate window), retry. Cap ~5 attempts.
- **Relaunch Chrome between batches** to clear accumulated Radware bot score (the single most
  effective recovery lever). The persistent profile keeps the SSO cookies across relaunches,
  so re-launching Chrome does NOT log you out.
- **Back off on hard captcha:** if you actually land on `validate.perfdrive.com`, stop
  hammering — wait 5–10 min for the bot score to decay before resuming. Repeated fast
  kill/relaunch cycles make the score worse.
- **Expect proxy-side suspensions under heavy use** (the shared proxy IP can get
  rate-limited/suspended). Detect a run of consecutive failures and back off long (minutes)
  rather than spinning.

## Fetch mechanics (browser in-page fetch, same as any Radware site)

Do the actual PDF download **inside the page** via `page.evaluate()` running
`fetch(url,{credentials:'include'})`, read the ArrayBuffer, base64 back to Python — this uses
the browser's cookies + TLS fingerprint so the proxy + Radware treat it as a normal XHR. A
plain `requests`/`curl` will fail (no proxy session cookies, wrong fingerprint).

```js
async (u) => {
  try {
    const r = await fetch(u, {credentials:'include', redirect:'follow',
                             headers:{'Accept':'application/pdf,*/*'}});
    const b = new Uint8Array(await r.arrayBuffer());
    let s=''; const C=0x8000;
    for (let i=0;i<b.length;i+=C) s+=String.fromCharCode.apply(null,b.subarray(i,Math.min(i+C,b.length)));
    const head = String.fromCharCode.apply(null,b.subarray(0,5));
    const url  = r.url || '';
    const bot  = url.includes('perfdrive')
              || String.fromCharCode.apply(null,b.subarray(0,300)).toLowerCase().includes('bot manager');
    const login = url.includes('mapmyaccess.com/login');   // <-- session-expiry signal
    return {status:r.status, len:b.length, head:head, bot:bot, login:login, b64:btoa(s)};
  } catch(e){ return {status:0,len:0,head:'',bot:false,login:false,b64:''}; }
}
```

Valid PDF iff `head === "%PDF-"` and `len > 10000`. Branch on `login` (re-auth), `bot`
(cooldown), `status === 404` (skip fast, below).

## 404 = skip immediately (do not retry)

A 404 means the article PDF genuinely doesn't exist (retracted / online-first without a
typeset PDF). Retrying it 5× with cooldowns burns the batch budget and starves reachable
articles. On `status === 404`, skip on the first attempt and record it to `known_404.json`
so the supervisor's "remaining" calc can exclude it and the loop can terminate.

## Preflight

At batch start: after ensuring login, do a **preflight PDF fetch** of one known-good DOI
(CLI arg). If it isn't `%PDF-`: if it's a login redirect, re-auth and retry once; if it's a
proxy error / suspension, print a clear message and abort the batch fast (don't grind the
whole queue failing). Also fetch `https://api.ipify.org` from the page and log the egress IP
for diagnostics (with the proxy this is the shared proxy IP, useful when debugging
throttling).

## Manifest / outputs / CLI (same as the standard IOP tool)

- Manifest `iop_<issn>_dois.json`: `{journal, issn, issues:{"<vol>-<iss>":[{doi,year,volume,issue,page}]}}`.
  Build from Crossref (`api.crossref.org/journals/<issn>/works`) and/or OpenAlex. **Bucket by
  issue cover-year, not online-first date**; audit completeness **by volume vs each issue TOC**.
- Output tree: `iop_<issn>_downloads/V<vol>I<iss>/<sanitized-doi>.pdf` (non-`[A-Za-z0-9._-]`→`_`);
  no-vol articles → `V_I_/`. Skip existing files > 10 KB (resume mechanism).
- CLI:
  ```
  python iop_download.py --manifest iop_0953-8984_dois.json \
    --dldir iop_0953-8984_downloads \
    --base https://iopscience-iop-org.jmi.mapmyaccess.com \
    --cdp  http://127.0.0.1:9223 \
    --preflight-doi 10.1088/1361-648x/ab7f6e \
    --min-year 2023 --max-year 2026 \
    --conc 1 --delay 2.2 --budget 480
  ```
  Budget = wall-seconds/invocation then exit 0 cleanly (restartable). Queue sorted by
  `(year,vol,iss,doi)`; 404-skip so dead early items can't block later years. Distinct exit
  code for session-dead / no-access so the supervisor reacts.

## Supervisor `run_loop.py`

Runs batches until the manifest (minus known-404s) is satisfied:

1. remaining = manifest articles in [min,max] year without a valid local PDF, excluding
   `known_404.json`. Stop at 0.
2. Ensure debug Chrome is up on the port (persistent user-data-dir); launch if not.
3. **Proactive re-login** if `now - last_login > 25 min` OR a login page is detected; on
   success reset `last_login`.
4. Run one `iop_download.py` batch.
5. **Kill + relaunch Chrome** between every batch (Radware reset; SSO cookies survive in the
   persistent profile).
6. Loop.

Windows robustness requirements (all learned the hard way):

- **Detached execution** so it survives the terminal closing: spawn with
  `DETACHED_PROCESS | CREATE_NO_WINDOW`, write PID to a file, append stdout/stderr to a log.
  `launch_detached.py` no-ops if already running (check PID via OpenProcess/WaitForSingleObject).
- **No terminal pop-ups:** every child `subprocess` (batch + login helper) passes
  `creationflags=CREATE_NO_WINDOW`; a detached parent has no console, so console children
  otherwise spawn visible windows.
- **Kill the whole Chrome tree by profile, not by port.** Killing only the port-listener
  leaks renderer/GPU children; over dozens of batches they exhaust the Windows commit charge
  / paging file (`0x800705AF "paging file too small"`, then even subprocess spawns fail).
  Kill every `chrome.exe` whose command line contains the debug user-data-dir path — targets
  only our instance, never the user's other Chrome:
  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
    Where-Object { $_.CommandLine -like '*<profile-leaf-name>*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  ```
- **Memory guard** on low-RAM machines: optionally check commit charge before launching
  Chrome; wait/retry if near the limit instead of crashing.

## known-404 + audit

- Record 404-skipped DOIs to `known_404.json`; exclude from "remaining" so the loop
  terminates; log the final skipped-404 count (never silently treat 404s as done).
- `audit.py`: per-volume/issue compare local valid-PDF count to expected article count, print
  gaps. Audit **by volume**, not by year.

## Acceptance criteria

1. From a **home IP**, after a one-time manual Microsoft login seeds the profile, a cold run
   authenticates via the OAuth JS-click and downloads a full year to the right
   `V<vol>I<iss>/` folders, resumable after Ctrl-C with no re-downloads.
2. The tool **survives session expiry**: after ~45 min it re-logins on the 25-min timer and
   keeps downloading — it never silently stalls for an hour on expired session (verify by
   letting it run > 1 h and confirming the newest file timestamp keeps advancing).
3. A 404 article skips in one attempt (logged); the run reaches later years instead of
   stalling.
4. A hard `perfdrive` captcha triggers a multi-minute back-off, not a tight kill/relaunch loop.
5. Multi-hour supervisor leaks no Chrome, pops no terminal windows, and never paging-crashes.
6. Re-run after completion is a fast no-op.
7. All access via the authenticated proxy browser session; no hardcoded creds, no shadow libs.

## Deliverables

`build_manifest.py`, `iop_download.py`, `run_loop.py`, `launch_detached.py`, `mma_login.py`,
`audit.py`, `test_access.py` (fetches 2–3 sample DOIs through the live CDP Chrome, prints
status/content-type and whether it hit a login redirect), plus a README with: the one-time
manual-login seeding step, the exact run commands, and the URL-rewrite rule.
