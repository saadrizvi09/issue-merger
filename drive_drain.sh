#!/usr/bin/env bash
# Drain-upload a local folder to Google Drive through Drive's bulk-upload throttle.
#
# Google Drive lets a single account upload a burst (~1-2 GB) at full speed, then
# silently throttles to ~0 until a cooldown passes. rclone shows no error - it
# just crawls. The fix that needs no quality loss: upload in timed bursts with a
# cooldown between them. Each session is resume-safe (already-uploaded files skip),
# so we just repeat until everything is up.
#
# Usage: bash drive_drain.sh <local_folder> <drive_folder_id> [remote_subpath]
set -u
RCLONE="C:/Users/acer/AppData/Local/Microsoft/WinGet/Packages/Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe/rclone-v1.74.3-windows-amd64/rclone.exe"
SRC="${1:?local folder}"
FOLDER="${2:?drive folder id}"
DEST="${3:-$(basename "$SRC")}"      # remote subfolder name (default: source folder name)

want=$(find "$SRC" -name "*.pdf" | wc -l)
for cycle in $(seq 1 40); do
  have=$("$RCLONE" lsf "gdrive:$DEST" --drive-root-folder-id "$FOLDER" 2>/dev/null | grep -c "\.pdf")
  echo "[drain] cycle $cycle  drive=$have/$want  $(date +%H:%M:%S)"
  if [ "$have" -ge "$want" ]; then
    echo "[drain] COMPLETE: all $want files in Drive."
    exit 0
  fi
  # one timed burst (stops itself after 4m whether it stalled or not)
  "$RCLONE" copy "$SRC" "gdrive:$DEST" \
      --drive-root-folder-id "$FOLDER" \
      --transfers 4 --drive-chunk-size 16M --tpslimit 10 --no-traverse \
      --max-duration 4m --stats 20s --stats-one-line 2>&1 | tail -2
  taskkill //F //IM rclone.exe >/dev/null 2>&1
  sleep 120                          # cooldown so the throttle window resets
done
echo "[drain] stopped after 40 cycles; re-run to continue (resume-safe)."
