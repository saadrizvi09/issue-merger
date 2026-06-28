# Auto-upload merged PDFs to Google Drive

Merged PDFs are written locally as:

```
Downloads/<YEAR> <JOURNAL>/V<vol>I<iss>Ar<n>.pdf
```

e.g. `Downloads/2025 RIM/V33I1Ar9.pdf`. The uploader mirrors every
`<YEAR> <JOURNAL>` folder into your Google Drive folder, creating the folders
automatically and **skipping anything already uploaded** (so re-runs are cheap).

Your Drive folder: <https://drive.google.com/drive/folders/1Yj54yG0JJI8ihUyL5KZEx928UYeVXNwH>
Folder ID: `1Yj54yG0JJI8ihUyL5KZEx928UYeVXNwH`

We use **rclone** (free) as the upload engine — no Google Cloud project needed.

---

## One-time setup (≈3 minutes)

### 1. Install rclone
```powershell
winget install Rclone.Rclone
```
(or download the zip from https://rclone.org/downloads/ and put `rclone.exe` on your PATH)

### 2. Connect your Google account
```powershell
rclone config
```
Answer the prompts:
- `n`  → New remote
- name → **`gdrive`**
- Storage → type **`drive`** (Google Drive)
- `client_id` → leave blank (press Enter)
- `client_secret` → leave blank
- `scope` → **`1`** (full access)
- `service_account_file` → leave blank
- Edit advanced config? → **`n`**
- Use web browser to authenticate? → **`y`** → a browser opens.
  **Log in with the account that has edit access to the shared folder**
  (the one shown in your Drive — `saadrizvi1234@gmail.com`).
- Configure this as a Shared Drive (Team Drive)? → **`n`**
- Keep this remote? → **`y`**, then `q` to quit.

> The Google account you log in with **must** be the one that can edit the
> shared folder, otherwise uploads will fail with "permission denied".

---

## ⚡ MUST DO for fast uploads: use your OWN Google client ID

By default rclone uses a **shared** OAuth client that Google rate-limits to a
crawl (~0.5 MiB/s — uploads burst ~1 GB then stall with no error). Giving rclone
your **own** free client ID fixes this completely (measured ~23 MiB/s, ~45×).
This is already configured on this machine, but to redo it / set it up elsewhere:

1. **https://console.cloud.google.com/** → create a project (e.g. `rclone`).
2. Search **"Google Drive API"** → **Enable**.
3. **APIs & Services → OAuth consent screen**: Audience **External**, fill app
   name + your emails → Save → then **Publish App** (else logins expire weekly).
4. **Clients → + Create client → Desktop app** → copy the **Client ID** + **Client secret**.
5. Put them on the remote and re-authorize:
   ```powershell
   rclone config update gdrive client_id "<CLIENT_ID>" client_secret "<CLIENT_SECRET>" --non-interactive
   # re-auth (old token is tied to the old client). Feed the 3 prompts; approve in browser:
   "y`ny`nn" | rclone config reconnect gdrive:
   ```
   In the browser, sign in with the Drive owner account; the
   "Google hasn't verified this app" warning is normal → **Advanced → Go to rclone**.

---

## Upload

### Push everything currently in Downloads
Dry-run first (shows what *would* upload, changes nothing):
```powershell
python gdrive_sync.py --folder 1Yj54yG0JJI8ihUyL5KZEx928UYeVXNwH --dry-run
```
Then for real:
```powershell
python gdrive_sync.py --folder 1Yj54yG0JJI8ihUyL5KZEx928UYeVXNwH
```

### Download + merge + auto-upload in one go
Add `--drive <folderID>` to a normal run and it uploads when merging finishes:
```powershell
python ojsdl.py "https://vestnmath.dnu.dp.ua/index.php/rim/issue/view/33" --name RIM --drive 1Yj54yG0JJI8ihUyL5KZEx928UYeVXNwH
```

That's it. The same `<YEAR> <JOURNAL>` folders appear in your Drive folder, and
re-running only uploads new files.

---

## Notes
- **Already-uploaded files are skipped** (rclone compares name + size), so it's
  safe to run the upload repeatedly.
- Per-article temp PDFs (`Downloads/.cache/`), logs, and json are never uploaded.
- To pull files *down* from Drive to local (e.g. to restore local copies):
  ```powershell
  rclone copy gdrive: Downloads --drive-root-folder-id 1Yj54yG0JJI8ihUyL5KZEx928UYeVXNwH
  ```
