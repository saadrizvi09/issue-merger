#!/usr/bin/env python3
"""
gdrive_sync - push merged PDFs to a Google Drive folder using rclone.

Why rclone (vs the Google Drive API): no Google Cloud project / OAuth consent
screen to set up (just `rclone config`), parallel uploads, 64 MB chunks, and it
SKIPS files already present on Drive - so re-running only uploads what's new.

Layout convention (produced by ojsdl.py / fastdl.py):

    <out>/<YEAR> <JOURNAL>/V<vol>I<iss>Ar<n>.pdf      <- uploaded
    <out>/.cache/...                                  <- excluded
    <out>/*.log, *.json                               <- excluded

`push()` mirrors every "<YEAR> <JOURNAL>" folder under <out> into the Drive
folder identified by `folder_id` (which may be a "Shared with me" folder you
have edit access to).

One-time setup is in DRIVE_SETUP.md.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("gdrive")

# Things under <out> that must never be uploaded.
EXCLUDES = [".cache/**", "*.json", "*.log", "*.part", "*.sqlite*", "*.tmp"]


def have_rclone() -> bool:
    return shutil.which("rclone") is not None


def remote_exists(remote: str) -> bool:
    try:
        out = subprocess.run(["rclone", "listremotes"], capture_output=True,
                             text=True, timeout=30).stdout
        return f"{remote}:" in out
    except Exception:
        return False


def push(out_dir, folder_id: str, remote: str = "gdrive",
         transfers: int = 8, dry_run: bool = False) -> bool:
    """rclone copy <out_dir> -> Drive folder <folder_id>. Returns True on success.

    CRITICAL prerequisite for speed: the rclone remote MUST use your OWN Google
    OAuth client_id/secret (see DRIVE_SETUP.md). With rclone's built-in shared
    client, uploads crawl at ~0.5 MiB/s (Google rate-limits the shared quota);
    with a personal client_id they run ~23 MiB/s. No rclone flag fixes the shared
    client - it's the client_id, not the tuning.

    Tuned for many LARGE PDFs (tens to ~170 MB each):
      --transfers 8          parallel files (own quota -> safe to parallelize)
      --drive-chunk-size 64M  bigger chunks -> higher per-file throughput
      --no-traverse          check files individually instead of recursively
                             listing the whole destination tree (the folder
                             holds other journals; --fast-list there stalled)

    Re-runs are idempotent: already-uploaded files (name+size) are skipped, so
    an interrupted upload just resumes.
    """
    out_dir = Path(out_dir)
    if not have_rclone():
        log.error("rclone is not installed / not on PATH. See DRIVE_SETUP.md.")
        return False
    if not remote_exists(remote):
        log.error("rclone remote %r not configured. Run: rclone config  "
                  "(see DRIVE_SETUP.md)", remote)
        return False

    cmd = ["rclone", "copy", str(out_dir), f"{remote}:",
           "--drive-root-folder-id", folder_id,
           "--transfers", str(transfers),
           "--checkers", "8",
           "--drive-chunk-size", "64M",
           "--no-traverse",
           "--stats", "5s", "--stats-one-line"]
    for ex in EXCLUDES:
        cmd += ["--exclude", ex]
    if dry_run:
        cmd.append("--dry-run")

    log.info("Uploading %s -> Google Drive folder %s (rclone, transfers=%d)%s",
             out_dir, folder_id, transfers, " [dry-run]" if dry_run else "")
    try:
        rc = subprocess.run(cmd).returncode
    except KeyboardInterrupt:
        log.warning("Upload interrupted - re-run to resume (already-uploaded files skip).")
        return False
    if rc == 0:
        log.info("Drive upload complete.")
        return True
    log.error("rclone exited with code %d", rc)
    return False


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Push merged PDFs to Google Drive via rclone.")
    p.add_argument("--folder", required=True, help="Google Drive folder ID (from its URL)")
    p.add_argument("--out", default="Downloads", help="local directory to mirror (default: Downloads)")
    p.add_argument("--remote", default="gdrive", help="rclone remote name (default: gdrive)")
    p.add_argument("--transfers", type=int, default=8)
    p.add_argument("--dry-run", action="store_true", help="show what would upload, change nothing")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ok = push(args.out, args.folder, args.remote, args.transfers, args.dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
