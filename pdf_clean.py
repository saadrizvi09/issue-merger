#!/usr/bin/env python3
"""
pdf_clean - general-purpose cleaner/recolorer for merged academic-journal PDFs
(thousands of pages, fast, free).

WHY PyMuPDF (fitz): it is the fastest FREE PDF engine for this job. We never
rasterize a page (that would be slow AND destroy the vector text / balloon the
file). Every edit is done on the existing vector content:
  * text removal      -> redaction annotations (apply_redactions, IMAGE_NONE so
                         figures/logos survive)
  * link removal      -> delete the link annotation (page.delete_link)
  * link recolor      -> rewrite the colour operator in the raw content stream
                         (bytes regex, no re-layout) so the LINK STAYS ACTIVE and
                         only its visual colour changes.
  * green-dot removal -> paint the small green vector mark white (draw_rect)
This is O(content) per page and streams one page at a time, so 3000+ pages and
400 MB files are handled in a few minutes with flat memory.

RULES IMPLEMENTED (per the spec):

  GLOBAL (every page)
    - remove open-access declarations ("This is an open access article ...")
    - remove green dots (e.g. the ORCID green icon square)
    - remove hyperlinks located in the running header/footer margins

  ARTICLE FIRST PAGE ONLY
    - remove the DOI link entirely (annotation + visible text)
    - remove ALL other URL hyperlinks (annotation + visible text): orcid.org,
      any http(s)://, www., creativecommons, etc.
    - author e-mail addresses are KEPT (text preserved) by default
      (use --strip-emails to remove them too)

  ALL OTHER PAGES + REFERENCES
    - keep every link (DOI, references, URLs) ACTIVE and unchanged in position
    - only recolor their (blue) text to black

Usage:
  python pdf_clean.py "issue.pdf" -o "issue_cleaned.pdf"
  python pdf_clean.py "issue.pdf" -o out.pdf --samples samples/   # before/after PNGs
  python pdf_clean.py in.pdf -o out.pdf --strip-emails
"""
from __future__ import annotations
import argparse, re, sys, time
from pathlib import Path
import fitz  # PyMuPDF

fitz.TOOLS.mupdf_display_errors(False)   # silence noisy non-fatal parse warnings

# ---------------------------------------------------------------- colour logic #
def _bluish(r, g, b):
    """Blue/indigo link colour (e.g. Hindawi 0x2e3092 = .18,.19,.57)."""
    return b > 0.30 and b - max(r, g) > 0.12

_RG = re.compile(rb"(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+rg\b")
_K  = re.compile(rb"(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+k\b")
# scn with 3 components + a named colour space set just before via /CSx cs
_SCN3 = re.compile(rb"(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+scn\b")

def recolor_page(doc, page) -> int:
    """Blacken bluish fill colours in every content stream (RGB `rg`, CMYK `k`,
    and 3-component `scn`). Rewrites bytes only where a colour actually changed,
    so link annotations are untouched -> links stay active, text turns black."""
    total = 0
    for xref in page.get_contents():
        raw = doc.xref_stream(xref)
        cnt = [0]
        def rg(m):
            try: r, g, b = (float(x) for x in m.group(1, 2, 3))
            except ValueError: return m.group(0)
            if _bluish(r, g, b): cnt[0] += 1; return b"0 0 0 rg"
            return m.group(0)
        def kf(m):
            try: c, mm, y, k = (float(x) for x in m.group(1, 2, 3, 4))
            except ValueError: return m.group(0)
            r, g, b = (1-c)*(1-k), (1-mm)*(1-k), (1-y)*(1-k)
            if _bluish(r, g, b): cnt[0] += 1; return b"0 0 0 1 k"
            return m.group(0)
        def scn(m):
            try: r, g, b = (float(x) for x in m.group(1, 2, 3))
            except ValueError: return m.group(0)
            if _bluish(r, g, b): cnt[0] += 1; return b"0 0 0 scn"
            return m.group(0)
        new = _SCN3.sub(scn, _K.sub(kf, _RG.sub(rg, raw)))
        if cnt[0]:
            doc.update_stream(xref, new)
            total += cnt[0]
    return total

# ---------------------------------------------------------------- green dots #
def _greenish(c):
    if not c or len(c) != 3: return False
    r, g, b = c
    return g > 0.4 and g - max(r, b) > 0.15

def _orange(c):
    """The 'OPEN ACCESS' badge fill (e.g. 0.94, 0.40, 0.21)."""
    if not c or len(c) != 3: return False
    r, g, b = c
    return r > 0.80 and 0.20 < g < 0.65 and b < 0.42

def mark_rects(page, want_green=True, want_orange=True):
    """One get_drawings pass -> (green_dot_rects, orange_badge_rect).
      green: small green vector marks (ORCID icon). Size-capped so real green
             content inside figures/charts is never touched.
      orange: the rounded 'OPEN ACCESS' pill in the top masthead (multiple orange
              path pieces) merged into one bounding box; top-region + size-gated so
              orange figure data is never touched."""
    H, W = page.rect.height, page.rect.width
    greens, orange_parts = [], []
    for dr in page.get_drawings():
        f, col = dr.get("fill"), dr.get("color")
        r = fitz.Rect(dr["rect"])
        if want_green and (_greenish(f) or _greenish(col)):
            # Size-capped AND below the top masthead: journal LOGOS are green too
            # and can contain small (<=18pt) green pieces (e.g. the Hindawi 2022
            # mark). Restricting to y below the masthead band stops the logo from
            # being clipped. ORCID iD badges are covered separately via their link.
            if r.width <= 14 and r.height <= 14 and not r.is_empty and r.y0 > H * 0.14:
                greens.append(r)
        if want_orange and _orange(f):
            if r.y0 < H * 0.25 and r.width < 160 and r.height < 26:
                orange_parts.append(r)
    orange = None
    if orange_parts:
        orange = orange_parts[0]
        for r in orange_parts[1:]:
            orange |= r
    return greens, orange

# ---------------------------------------------------------------- first page #
DOI_HOST = ("doi.org",)
# anchor text that is itself a URL/DOI (safe to redact off the first page)
_URLISH_TEXT = re.compile(r"(https?://|www\.|doi\.org/|orcid\.org/|creativecommons\.org)", re.I)
# a line that is essentially just a bare URL/DOI (the article's own footer/DOI line)
_BARE_URL_LINE = re.compile(r"^\(?\s*(https?://|www\.|doi\s*:)\s*\S{4,}\s*\)?\.?$", re.I)
OPEN_ACCESS_STARTS = ("this is an open access", "this article is an open access",
                      "open access article distributed", "this is an open-access")

def is_first_page(page) -> bool:
    """Article first page = the article's OWN DOI link in the masthead (top
    region) PLUS a corroborating masthead element (license / ORCID / e-mail).

    The masthead DOI is a real https link; REFERENCE DOIs (which also appear near
    the top of a two-column reference list) are http:// and never sit next to a
    license/ORCID/e-mail link. Requiring both signals prevents a reference page
    from being mistaken for a first page (which would wrongly redact references)."""
    H = page.rect.height
    top_doi = corrob = False
    for l in page.get_links():
        u = (l.get("uri") or "").lower()
        r = fitz.Rect(l["from"])
        if u.startswith("https://") and "doi.org" in u and r.y0 < H * 0.25:
            top_doi = True
        if "creativecommons" in u or "orcid.org" in u or u.startswith("mailto:"):
            corrob = True
    return top_doi and corrob

def line_rect_containing(page, rect):
    """Full text-line bbox that contains `rect` (so we redact the whole visible
    URL line, not just the annotation box)."""
    best = None
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            lr = fitz.Rect(ln["bbox"])
            if lr.intersects(rect):
                best = lr if best is None else best | lr
    return best or rect

def open_access_rects(page):
    """Word-precise rects for the open-access DECLARATION sentence
    ("This is an open access article ... provided the original work is properly
    cited."). It STARTS at the words "This ... open access" and ENDS at the
    sentence's final "cited" (or a creativecommons URL), with a copyright hard-stop
    and a word cap as backstops.

    Starting at "This" is what preserves the COPYRIGHT text in BOTH layouts:
      * Wiley: the sentence sits on its own line(s), copyright is a separate line.
      * Hindawi: the sentence is appended to the copyright line -
        "Copyright (c) 2021 A. Author et al. This is an open access article ...".
        The "Copyright ... et al." prefix precedes "This", so it is never redacted;
        only the open-access clause after it is removed.
    Ending at "cited" (not a fixed word count) is what stops the redaction from
    over-running into whatever follows the sentence."""
    ws = page.get_text("words")            # x0,y0,x1,y1,word,block,line,wno
    rects, N, i = [], len(ws), 0
    def is_oa(k):
        w = ws[k][4].lower()
        return ("open-access" in w) or ("open" in w and k + 1 < N and "access" in ws[k + 1][4].lower())
    while i < N:
        if is_oa(i):
            # Anchor on "open access" (the word "This" is unreliable: some fonts
            # ligature "Th", so get_text returns a mangled token). Walk BACK over up
            # to 3 short connective words ("This is an"), stopping at the copyright
            # boundary ("... et al." / "Copyright" / (c)) so the copyright prefix
            # is preserved.
            start, steps = i, 0
            while start - 1 >= 0 and steps < 3:
                pw = ws[start - 1][4]; pwl = pw.lower()
                if pwl == "et" or pwl.endswith("al.") or "copyright" in pwl or "©" in pw:
                    break
                if len(pw) > 8:                        # don't swallow long body words
                    break
                start -= 1; steps += 1
            # Walk FORWARD to the sentence end ("... properly cited." / cc URL). Only
            # commit if that terminator is found within the window, so an incidental
            # "open access" mention in the body never triggers a runaway redaction.
            j, cap, cand, ok = start, min(i + 70, N), [], False
            while j < cap:
                tl = ws[j][4].lower()
                if tl.startswith("copyright"):
                    break
                cand.append(fitz.Rect(ws[j][:4]))
                if "cited" in tl or "creativecommons" in tl:
                    ok = True; j += 1; break
                j += 1
            if ok:
                rects.extend(cand)
            i = max(j, i + 1)
            continue
        i += 1
    return rects

# ---------------------------------------------------------------- per page #
def clean_page(doc, page, is_first, header_band, footer_band, strip_emails,
               green_all=False, oa_all=False) -> dict:
    H = page.rect.height
    changed = {"recolored": 0, "green": 0, "orange": 0, "links_removed": 0, "redacted": 0}

    # 1) recolor bluish -> black (keeps links active). every page (cheap: bytes regex).
    changed["recolored"] = recolor_page(doc, page)

    # 2) green ORCID dots + orange 'OPEN ACCESS' badge -> white, in ONE drawings pass.
    #    Both live in the article masthead, so they are scanned on FIRST pages (green
    #    also on all pages if --green-all-pages). Scanning every body page for these
    #    marks is slower AND risks clipping green/orange markers inside figures, so it
    #    is scoped to where they actually occur.
    want_green = is_first or green_all
    want_orange = is_first        # the orange OPEN ACCESS pill is a first-page badge
    greens, orange = ([], None)
    if want_green or want_orange:
        greens, orange = mark_rects(page, want_green=want_green, want_orange=want_orange)
    orcid_covers = []   # green ORCID "iD" badges: covered via their orcid.org link

    # 3) header/footer hyperlink removal (running margins). every page.
    for l in list(page.get_links()):
        u = (l.get("uri") or "")
        if not u:
            continue
        r = fitz.Rect(l["from"])
        in_header = r.y1 < H * header_band
        in_footer = r.y0 > H * (1 - footer_band)
        if in_header or in_footer:
            page.delete_link(l); changed["links_removed"] += 1

    # open-access declaration removal: first pages by default (that is where the
    # "This is an open access article ..." sentence sits), opt-in for all pages.
    redrects = list(open_access_rects(page)) if (is_first or oa_all) else []

    # 4) FIRST PAGE: strip DOI + all URL links.
    #    - ALWAYS deactivate the link annotation (make it non-clickable).
    #    - REDACT the visible text ONLY when the anchor text is itself a URL/DOI.
    #      (An ORCID link is anchored on the AUTHOR'S NAME - deactivating removes
    #      the link but the name text must stay, so we don't redact those.)
    if is_first:
        for l in list(page.get_links()):
            u = (l.get("uri") or "").lower()
            if not u:
                continue
            if u.startswith("mailto:") and not strip_emails:
                continue                       # keep author e-mails
            r = fitz.Rect(l["from"])
            if "orcid.org" in u:               # the green ORCID iD badge sits ON
                orcid_covers.append(r)         # this link rect (dot OR Type3 icon)
            anchor = page.get_textbox(r).strip()
            if _URLISH_TEXT.search(anchor):    # visible text is a URL/DOI -> remove it
                redrects.append(r + (-1, -1, 1, 1))
            page.delete_link(l); changed["links_removed"] += 1
        # bare URL/DOI text lines with NO annotation (article's own doi/url line)
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                txt = "".join(s["text"] for s in ln.get("spans", [])).strip()
                if txt and _BARE_URL_LINE.match(txt):
                    redrects.append(fitz.Rect(ln["bbox"]))

    # apply text redactions (white fill, keep images/figures intact)
    applied = False
    for r in redrects:
        if not r.is_empty:
            page.add_redact_annot(r, fill=(1, 1, 1)); applied = True; changed["redacted"] += 1
    if applied:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    # paint green dots + orange badge white AFTER redaction so they aren't re-exposed
    for r in greens:
        page.draw_rect(r + (-0.5, -0.5, 0.5, 0.5), color=(1, 1, 1), fill=(1, 1, 1))
        changed["green"] += 1
    for r in orcid_covers:                     # ORCID iD badges (any representation)
        page.draw_rect(r + (-1.5, -1.5, 1.5, 1.5), color=(1, 1, 1), fill=(1, 1, 1))
        changed["green"] += 1
    if orange:
        page.draw_rect(orange + (-1, -1, 1, 1), color=(1, 1, 1), fill=(1, 1, 1))
        changed["orange"] += 1
    return changed

def process(src: Path, dst: Path, header_band, footer_band, strip_emails,
            samples: Path | None, green_all=False, oa_all=False, progress_every=400) -> dict:
    t0 = time.time()
    doc = fitz.open(src)
    n = doc.page_count
    tot = {"recolored": 0, "green": 0, "orange": 0, "links_removed": 0, "redacted": 0, "first_pages": 0}
    sample_pages = set()
    if samples:
        samples.mkdir(parents=True, exist_ok=True)
        # a first page + a reference-ish page for a before/after check
        sample_pages = {0, min(1, n-1), min(5, n-1)}
        for pi in sorted(sample_pages):
            doc[pi].get_pixmap(dpi=110).save(str(samples / f"p{pi}_before.png"))
    for pi in range(n):
        page = doc[pi]
        first = is_first_page(page)
        if first: tot["first_pages"] += 1
        c = clean_page(doc, page, first, header_band, footer_band, strip_emails,
                       green_all=green_all, oa_all=oa_all)
        for k in ("recolored", "green", "orange", "links_removed", "redacted"):
            tot[k] += c[k]
        if progress_every and (pi + 1) % progress_every == 0:
            print(f"  ...{pi+1}/{n} pages ({time.time()-t0:.0f}s)", flush=True)
    if samples:
        for pi in sorted(sample_pages):
            doc[pi].get_pixmap(dpi=110).save(str(samples / f"p{pi}_after.png"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dst, garbage=1, deflate=True)
    tot["pages"] = n
    tot["seconds"] = round(time.time() - t0, 1)
    tot["out_mb"] = round(dst.stat().st_size / 1e6, 1)
    doc.close()
    return tot

def _valid_pdf(path: Path, min_pages=1) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    try:
        d = fitz.open(path); ok = d.page_count >= min_pages; d.close(); return ok
    except Exception:
        return False

def process_chunked(src: Path, dst: Path, header_band, footer_band, strip_emails,
                    green_all, oa_all, chunk: int, work: Path) -> dict:
    """Resume-safe processing for very large PDFs: clean each page-range into its
    own part file (skipping parts already produced), then merge the parts. If the
    process is killed/timed-out, re-running continues from the first missing part."""
    work.mkdir(parents=True, exist_ok=True)
    base = fitz.open(src); n = base.page_count; base.close()
    ranges = [(a, min(a + chunk, n)) for a in range(0, n, chunk)]
    tot = {"recolored": 0, "green": 0, "orange": 0, "links_removed": 0, "redacted": 0, "first_pages": 0}
    parts = []
    for k, (a, b) in enumerate(ranges):
        part = work / f"part_{k:03d}_{a}_{b}.pdf"
        parts.append(part)
        if _valid_pdf(part, b - a):
            print(f"[chunk {k+1}/{len(ranges)}] pages {a}-{b}: already done, skipping", flush=True)
            continue
        t0 = time.time()
        d = fitz.open(src); d.select(range(a, b))
        for pi in range(d.page_count):
            pg = d[pi]; first = is_first_page(pg)
            if first: tot["first_pages"] += 1
            c = clean_page(d, pg, first, header_band, footer_band, strip_emails,
                           green_all=green_all, oa_all=oa_all)
            for key in ("recolored", "green", "orange", "links_removed", "redacted"):
                tot[key] += c[key]
        d.save(part, garbage=1, deflate=True); d.close()
        print(f"[chunk {k+1}/{len(ranges)}] pages {a}-{b}: done in {time.time()-t0:.0f}s "
              f"-> {part.name} ({part.stat().st_size/1e6:.0f} MB)", flush=True)
    # merge parts
    print(f"merging {len(parts)} parts -> {dst} ...", flush=True)
    t0 = time.time()
    out = fitz.open()
    for part in parts:
        pd = fitz.open(part); out.insert_pdf(pd); pd.close()
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, garbage=1, deflate=True)
    tot["pages"] = out.page_count; out.close()
    tot["out_mb"] = round(dst.stat().st_size / 1e6, 1)
    tot["seconds"] = round(time.time() - t0, 1)
    print(f"merged in {tot['seconds']}s", flush=True)
    return tot

# -- automatic QC: catch silently-broken output before it ships ------------- #
def _green_frac(page, band, dpi=40):
    """Fraction of bright-green pixels in a region (0..1). ORCID iD badges AND the
    green parts of journal logos are bright green, so this one measure catches BOTH
    failure modes: a badge LEFT behind (green remains near authors) and a logo
    CLIPPED (green lost from the masthead)."""
    try:
        pm = page.get_pixmap(dpi=dpi, clip=band)          # RGB(A)
    except Exception:
        return 0.0
    s, st = pm.samples, pm.n
    n = pm.width * pm.height
    if not n:
        return 0.0
    cnt = 0
    for i in range(0, len(s), st):
        r, g, b = s[i], s[i + 1], s[i + 2]
        # green incl. lime/chartreuse (ORCID badge renders ~(197,217,78)): the
        # reliable signal is a big green-over-blue gap; g-r can be small for lime.
        if g > 110 and (g - b) > 45 and (g - r) > -25:
            cnt += 1
    return cnt / n

def qc_report(src_path, out_path, sample=10):
    """Verify the edit invariants on a sample of first pages by comparing the
    ORIGINAL and the cleaned output. Catches the exact failure modes we have hit:
      - copyright line accidentally removed
      - open-access sentence NOT removed
      - ORCID badge left behind (missed template - green remains near authors)
      - journal LOGO clipped by green-dot removal (green lost from masthead)
    Returns a list of (page_number, message). Empty list = clean."""
    src = fitz.open(src_path); out = fitz.open(out_path)
    firsts = [i for i in range(src.page_count) if is_first_page(src[i])]
    issues = []
    if firsts and src.page_count == out.page_count:
        step = max(1, len(firsts) // sample)
        for pi in firsts[::step][:sample]:
            sp, op = src[pi], out[pi]; W, H = sp.rect.width, sp.rect.height
            ot = op.get_text().lower()
            if "copyright" not in ot:
                issues.append((pi, "copyright line missing"))
            if "open access article" in ot or "properly cited" in ot:
                issues.append((pi, "open-access sentence still present"))
            # ORCID badge left: check each of the SOURCE's orcid-link spots in the
            # OUTPUT at high dpi (small, targeted - immune to the anti-aliasing that
            # washes out tiny badges in a low-dpi full-band scan).
            for l in sp.get_links():
                if "orcid.org" in (l.get("uri") or "").lower():
                    spot = fitz.Rect(l["from"]) + (-1, -1, 1, 1)
                    if _green_frac(op, spot, dpi=150) > 0.05:
                        issues.append((pi, "ORCID badge still present (not covered)"))
                        break
            # logo damage: masthead lost most of its green vs the original
            mast = fitz.Rect(W * 0.35, 0, W, H * 0.16)
            sg, og = _green_frac(sp, mast), _green_frac(op, mast)
            if sg > 0.003 and og < sg * 0.7:
                issues.append((pi, f"masthead logo lost green ({sg:.4f}->{og:.4f}) - possible logo damage"))
    src.close(); out.close()
    return issues

def print_qc(src_path, dst_path):
    issues = qc_report(src_path, dst_path)
    if issues:
        print("\n[!] QC WARNINGS - eyeball these first pages before shipping:")
        for pi, msg in issues:
            print(f"    page {pi+1}: {msg}")
    else:
        print("\nQC: sampled first pages OK (copyright kept, open-access removed, "
              "ORCID badge removed, logo intact).")
    return issues

def main(argv=None):
    ap = argparse.ArgumentParser(description="Clean/recolor merged journal PDFs (fast, free).")
    ap.add_argument("src")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--header-band", type=float, default=0.045,
                    help="top fraction treated as running header (link removal). "
                         "Kept small so column-top reference DOIs are NOT removed.")
    ap.add_argument("--footer-band", type=float, default=0.045,
                    help="bottom fraction treated as running footer (link removal).")
    ap.add_argument("--strip-emails", action="store_true",
                    help="also remove author e-mail (mailto) links on the first page")
    ap.add_argument("--green-all-pages", action="store_true",
                    help="scan EVERY page for green dots (slower; may clip green figure markers)")
    ap.add_argument("--open-access-all-pages", action="store_true",
                    help="scan EVERY page for open-access declarations (slower)")
    ap.add_argument("--samples", help="dir for before/after sample PNGs")
    ap.add_argument("--chunk", type=int, default=0,
                    help="resume-safe chunked mode: process this many pages per part "
                         "file, then merge (recommended for thousands of pages)")
    ap.add_argument("--work", help="work dir for chunk part files (default: <out>.parts)")
    ap.add_argument("--limit", type=int, default=0, help="only first N pages (debug)")
    args = ap.parse_args(argv)

    src, dst = Path(args.src), Path(args.out)
    if args.limit:
        d = fitz.open(src); d.select(range(min(args.limit, d.page_count)))
        tmp = dst.with_suffix(".trunc.pdf"); d.save(tmp); d.close(); src = tmp
    print(f"cleaning {src.name} -> {dst}")
    if args.chunk:
        work = Path(args.work) if args.work else dst.with_suffix(".parts")
        r = process_chunked(src, dst, args.header_band, args.footer_band, args.strip_emails,
                            args.green_all_pages, args.open_access_all_pages, args.chunk, work)
    else:
        r = process(src, dst, args.header_band, args.footer_band, args.strip_emails,
                    Path(args.samples) if args.samples else None,
                    green_all=args.green_all_pages, oa_all=args.open_access_all_pages)
    print_qc(str(src), str(dst))
    print(f"\nDONE {r['pages']} pages in {r['seconds']}s -> {dst} ({r['out_mb']} MB)")
    print(f"  first pages cleaned : {r['first_pages']}")
    print(f"  links removed       : {r['links_removed']}")
    print(f"  text lines redacted : {r['redacted']}")
    print(f"  green dots removed  : {r['green']}")
    print(f"  orange badges removed: {r['orange']}")
    print(f"  blue ops -> black   : {r['recolored']}")

if __name__ == "__main__":
    main()
