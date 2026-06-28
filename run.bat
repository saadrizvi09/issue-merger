@echo off
REM ===========================================================
REM  Journal PDF Downloader - double-click launcher (Windows)
REM  No commands to memorize: just run this file and paste a
REM  journal ISSN or its archive URL when asked.
REM ===========================================================
setlocal
cd /d "%~dp0"

echo ===========================================================
echo   Journal PDF Downloader
echo ===========================================================
echo.

REM --- 1. Make sure Python is available -----------------------
where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python is not installed.
  echo     Install it from https://www.python.org/downloads/
  echo     IMPORTANT: during setup, tick "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

REM --- 2. Install the libraries it needs (safe to re-run) ------
echo Checking required libraries...
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
echo.

REM --- 3. Ask what to download --------------------------------
echo You can paste EITHER:
echo    - the journal's ISSN        e.g.  2093-3827
echo    - or its archive page URL   e.g.  https://www.kjpp.net/journal/archives.html
echo.
set /p TARGET="Paste it here and press Enter: "
if "%TARGET%"=="" (
  echo Nothing entered. Exiting.
  pause
  exit /b 1
)

echo.
echo Starting download. This can take a while for a full journal.
echo (You can close this window any time; re-running resumes where it left off.)
echo.
python journaldl.py "%TARGET%"

echo.
echo ===========================================================
echo  Finished. Your PDFs are in the "Downloads" folder next to
echo  this launcher.
echo ===========================================================
pause
