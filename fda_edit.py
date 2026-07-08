#!/usr/bin/env python3
"""
fda_edit - clean up Journal of Food & Drug Analysis merged issue PDFs.

Three edits per file (originals are never touched; output mirrors the tree under
a new root):

 1. DELETE cover pages  - the bepress/DigitalCommons cover sheet that precedes
    every article (detected by "Recommended Citation" + "Follow this and
    additional works"). Removes all of them.

 2. CLEAN the article first page - redact the ScienceDirect banner text, the
    "journal homepage" line, the email, the article DOI line, and the WHOLE
    "This is an open access article ... license (...)" line; remove the URL/email
    link annotations; and cover the "Check for updates" (crossmark) logo.

 3. RECOLOR blue -> black - the [1-3] citation numbers and reference numbers/links
    are drawn with a blue fill color in the content stream; we rewrite every
    bluish fill-color operator to black (perfect fidelity, no re-layout).

Usage:
  python fda_edit.py "C:/Users/acer/Downloads/FDA" --out "C:/Users/acer/Downloads/FDA_edited"
  python fda_edit.py "<one.pdf>" --out <dir>            # single file
  python fda_edit.py ... --samples                       # also dump before/after PNGs
"""
from __future__ import annotations
import argparse, glob, os, re, sys
from pathlib import Path
import fitz  # PyMuPDF

# -- cover-page detection ---------------------------------------------------- #
def is_cover(page) -> bool:
    t = page.get_text()
    return ("Recommended Citation" in t and "Follow this and additional works" in t)

# -- things to scrub off the article first page ------------------------------ #
# phrases whose line(s) get redacted (text removed, figures preserved)
SCRUB_PHRASES = [
    "Available online at www.sciencedirect.com",
    "www.sciencedirect.com",
    "journal homepage:",
    "www.jfda-online.com",
    "This is an open access article",
    "creativecommons.org",
]
URLISH = re.compile(r"(https?://|www\.|doi\.org|creativecommons|sciencedirect|jfda-online)", re.I)

def line_rects_for(page, phrase):
    """Return the full line bbox(es) that contain `phrase`."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            txt = "".join(s["text"] for s in ln.get("spans", []))
            if phrase.lower() in txt.lower():
                out.append(fitz.Rect(ln["bbox"]))
    return out

def open_access_word_rects(page):
    """Precise rects for the sentence 'This is an open access article ... creativecommons...'
    (may wrap across lines), leaving any copyright text on the same line intact."""
    rects = []
    ws = page.get_text("words")              # x0,y0,x1,y1,word,block,line,wno
    i = 0
    while i < len(ws):
        if (ws[i][4] == "This" and i + 4 < len(ws)
                and ws[i + 3][4] == "open" and ws[i + 4][4] == "access"):
            j = i
            end = min(i + 16, len(ws))
            while j < end and "creativecommons" not in ws[j][4].lower():
                j += 1
            for w in ws[i:j + 1]:
                rects.append(fitz.Rect(w[:4]))
            i = j + 1
        else:
            i += 1
    return rects

def banner_rect(page):
    """The light-grey ScienceDirect banner box in the top region (logos sit outside it)."""
    H, W = page.rect.height, page.rect.width
    for dr in page.get_drawings():
        f = dr.get("fill")
        if not f:
            continue
        r = fitz.Rect(dr["rect"])
        grey = min(f) > 0.82 and (max(f) - min(f)) < 0.06
        if grey and r.y0 < H * 0.30 and r.width > W * 0.20 and r.height > 10:
            return r
    return None

def banner_box(page):
    """The light-grey ScienceDirect banner box: returns (rect, fill) or (None, None)."""
    H, W = page.rect.height, page.rect.width
    for dr in page.get_drawings():
        f = dr.get("fill")
        if not f:
            continue
        r = fitz.Rect(dr["rect"])
        grey = min(f) > 0.82 and (max(f) - min(f)) < 0.06
        if grey and r.y0 < H * 0.30 and r.width > W * 0.20 and r.height > 10:
            return r, tuple(f)
    return None, None

AVAIL_RE = re.compile(r"available online\s+\d", re.I)          # 'Available online 21 March 2018'
DOI_LINE_RE = re.compile(r"^https?://doi\.org/\S+$", re.I)     # standalone DOI url line

def clean_page(page, keep_banner=False):
    """Remove journal boilerplate from ANY page (runs on every page, not just
    article first pages):
      - the 'This is an open access article ... license (...)' sentence,
      - the 'Available online <date>' line,
      - the article's own DOI line (a bare doi.org URL, or any j.jfda DOI),
      - on the banner page: the two ScienceDirect banner URL lines,
      - the 'Check for updates' (crossmark) logo, and (unless keep_banner) the
        whole grey ScienceDirect banner box.
    EMAILS ARE KEPT. Reference DOIs/lines are left alone (patterns are precise:
    only bare doi.org URLs or this journal's own j.jfda DOI are touched).
    Returns 1 if anything was changed on the page."""
    band_rect, band_fill = banner_box(page)
    band_fill = band_fill or (0.9, 0.9, 0.9)
    redrects = []   # list of (rect, fill)
    # open-access sentence (word-precise), anywhere
    for r in open_access_word_rects(page):
        redrects.append((r, (1, 1, 1)))
    # the two banner URL lines -> blend with banner grey (keep) or white (remove)
    band_line_fill = band_fill if keep_banner else (1, 1, 1)
    for ph in ["Available online at www.sciencedirect.com", "journal homepage:"]:
        for r in line_rects_for(page, ph):
            redrects.append((r, band_line_fill))
    # per-line: 'Available online <date>' + the article's own DOI line
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            txt = "".join(s["text"] for s in ln.get("spans", [])).strip()
            if not txt:
                continue
            low = txt.lower()
            # Only a STANDALONE doi.org URL line (the article's own footer DOI).
            # Never match reference DOIs (embedded in citation lines) so we don't
            # delete reference entries.
            is_doi = bool(DOI_LINE_RE.match(txt))
            is_avail = bool(AVAIL_RE.search(low)) and "sciencedirect" not in low
            if is_doi or is_avail:
                redrects.append((fitz.Rect(ln["bbox"]), (1, 1, 1)))
    # crossmark logo + (optional) banner box cover
    cover = []   # (rect, fill) painted on top after redaction
    if not keep_banner and band_rect:
        cover.append((band_rect, (1, 1, 1)))                  # remove whole grey box
    for l in page.get_links():
        if "crossmark" in (l.get("uri") or "").lower():
            cover.append((fitz.Rect(l["from"]), (1, 1, 1)))   # check-for-updates logo
    # drop URL / crossmark link annotations; KEEP mailto (emails) and (if keeping) banner links
    for l in page.get_links():
        uri = (l.get("uri") or "").lower()
        if not uri or uri.startswith("mailto:"):
            continue
        if keep_banner and ("sciencedirect" in uri or "jfda-online" in uri):
            continue
        if URLISH.search(uri) or "crossmark" in uri:
            page.delete_link(l)
    applied = False
    for r, fill in redrects:
        if not r.is_empty:
            page.add_redact_annot(r, fill=fill); applied = True
    if applied:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    for r, fill in cover:
        page.draw_rect(r + (-1, -1, 1, 1), color=fill, fill=fill)
    return 1 if (applied or cover) else 0

# -- blue -> black via content-stream color operators ------------------------ #
def _bluish(r, g, b):
    return b > 0.30 and b - max(r, g) > 0.12

_RG = re.compile(rb"(-?[0-9.]+) (-?[0-9.]+) (-?[0-9.]+) rg\b")
_K = re.compile(rb"(-?[0-9.]+) (-?[0-9.]+) (-?[0-9.]+) (-?[0-9.]+) k\b")

def recolor_page(doc, page):
    """Blacken bluish fill colors directly in each content stream (no clean_contents,
    which is the slow part). Works on bytes; only rewrites streams that changed."""
    total = 0
    for xref in page.get_contents():
        raw = doc.xref_stream(xref)
        cnt = [0]
        def rg(m):
            try:
                r, g, b = (float(x) for x in m.group(1, 2, 3))
            except ValueError:
                return m.group(0)
            if _bluish(r, g, b):
                cnt[0] += 1; return b"0 0 0 rg"
            return m.group(0)
        def kf(m):
            try:
                c, mg, y, k = (float(x) for x in m.group(1, 2, 3, 4))
            except ValueError:
                return m.group(0)
            r, g, b = (1 - c) * (1 - k), (1 - mg) * (1 - k), (1 - y) * (1 - k)
            if _bluish(r, g, b):
                cnt[0] += 1; return b"0 0 0 1 k"
            return m.group(0)
        new = _K.sub(kf, _RG.sub(rg, raw))
        if cnt[0]:
            doc.update_stream(xref, new)
            total += cnt[0]
    return total

# -- per-file pipeline ------------------------------------------------------- #
def process(src: Path, dst: Path, keep_banner=False) -> dict:
    doc = fitz.open(src)
    n0 = doc.page_count
    covers = [p.number for p in doc if is_cover(p)]
    # clean boilerplate on EVERY page (covers get deleted anyway) + recolor everything.
    # Boilerplate (open-access, 'Available online <date>', the journal's own DOI,
    # banner) appears on article first pages AND editorial/guest-editor pages, so a
    # first-page-only pass missed some — scan all pages.
    cleaned = 0
    recolored = 0
    for p in doc:
        cleaned += clean_page(p, keep_banner=keep_banner)
        recolored += recolor_page(doc, p)
    # delete covers in one batch (far faster than one-by-one)
    if covers:
        doc.delete_pages(sorted(covers))
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dst, garbage=1, deflate=True)
    res = dict(file=src.name, pages_before=n0, covers=len(covers),
               pages_after=doc.page_count, first_pages_cleaned=cleaned,
               blue_ops_recolored=recolored)
    doc.close()
    return res

def main(argv=None):
    ap = argparse.ArgumentParser(description="Clean JFDA merged-issue PDFs.")
    ap.add_argument("src", help="a PDF file or a folder (recursed)")
    ap.add_argument("--out", required=True, help="output root (mirrors folder tree)")
    ap.add_argument("--limit", type=int, default=0, help="process only first N files (testing)")
    ap.add_argument("--keep-banner", action="store_true",
                    help="keep the ScienceDirect banner instead of removing it")
    ap.add_argument("--overwrite", action="store_true",
                    help="reprocess even if the output already exists")
    args = ap.parse_args(argv)

    src = Path(args.src); out = Path(args.out)
    if src.is_file():
        files = [src]; root = src.parent
    else:
        files = [Path(f) for f in sorted(glob.glob(str(src / "**" / "*.pdf"), recursive=True))]
        root = src
    if args.limit:
        files = files[:args.limit]
    print(f"{len(files)} file(s) to process -> {out}")
    tot_c = tot_b = 0
    for i, f in enumerate(files, 1):
        rel = f.relative_to(root)
        dst = out / rel
        # resume: skip files already produced (valid, non-empty PDF) unless --overwrite
        if not args.overwrite and dst.exists() and dst.stat().st_size > 1024:
            try:
                _d = fitz.open(dst); ok = _d.page_count > 0; _d.close()
            except Exception:
                ok = False
            if ok:
                print(f"[{i}/{len(files)}] {rel}: already done, skipping")
                continue
        try:
            r = process(f, dst, keep_banner=args.keep_banner)
            tot_c += r["covers"]; tot_b += r["blue_ops_recolored"]
            print(f"[{i}/{len(files)}] {rel}: {r['pages_before']}->{r['pages_after']} "
                  f"pages (-{r['covers']} covers), {r['first_pages_cleaned']} first-pages cleaned, "
                  f"{r['blue_ops_recolored']} blue->black")
        except Exception as e:
            print(f"[{i}/{len(files)}] {rel}: ERROR {type(e).__name__}: {e}")
    print(f"\nDONE. removed {tot_c} cover pages, recolored {tot_b} blue ops across {len(files)} files.")

if __name__ == "__main__":
    main()
