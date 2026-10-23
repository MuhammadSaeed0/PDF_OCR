# PDF OCR â€” scanned PDFs â†’ searchable PDFs

PDF OCR watches a folder of scanned (image-only) PDFs and saves a searchable copy of
each one, with the same file name, in another folder. It uses
[OCRmyPDF](https://ocrmypdf.readthedocs.io/) with the [Tesseract](https://github.com/tesseract-ocr/tesseract)
OCR engine. English and Arabic are enabled by default.

* The original scan images stay as they are. An invisible text layer is added on top, so you can
  search, select and copy text in any PDF reader.
* Page order, page sizes and appearance are kept (`output_type = pdf`, lossless optimisation only).
* The original PDFs are never modified.
* New PDFs are picked up automatically. A file is never processed twice. Deleted or damaged
  outputs and changed source files are detected and regenerated.
* It is built to run unattended: crash-safe outputs, a persistent state file, automatic retries,
  a rotating log file, and a lock so only one copy runs per folder pair.
* There is a command-line mode (`run.bat`) and a simple Windows window (`run_gui.bat`).

---

## Contents

1. [Project structure](#1-project-structure)
2. [Requirements](#2-requirements)
3. [Quick installation (install.bat)](#3-quick-installation-installbat)
4. [Manual installation](#4-manual-installation)
5. [Verifying the installation](#5-verifying-the-installation)
6. [Configuration](#6-configuration)
7. [Running the application](#7-running-the-application)
8. [How duplicate prevention and change detection work](#8-how-duplicate-prevention-and-change-detection-work)
9. [Testing scenarios](#9-testing-scenarios)
10. [Troubleshooting](#10-troubleshooting)
11. [Log messages](#11-log-messages)

---

## 1. Project structure

```
D:\Projects\PDF_OCR\
â”‚  config.ini              â†گ all settings (folders, languages, intervals, ...)
â”‚  requirements.txt        â†گ Python packages
â”‚  install.bat             â†گ one-click installer
â”‚  run.bat                 â†گ start monitoring (console)
â”‚  run_gui.bat             â†گ start the graphical window
â”‚  verify.bat              â†گ check the installation + real OCR test
â”‚  main.py                 â†گ entry point (CLI and --gui)
â”‚  verify_install.py       â†گ the installation check run by verify.bat
â”‚  README.md
â”‚
â”œâ”€ pdf_ocr\                â†گ application package
â”‚     __init__.py          version
â”‚     config.py            reads/validates config.ini, updates it from the GUI
â”‚     logging_setup.py     log file (rotating) + console
â”‚     dependencies.py      finds Tesseract/Ghostscript/language data, start-up checks
â”‚     pdf_utils.py         PDF inspection & validation (PyMuPDF), hashing, safe copy
â”‚     ocr_engine.py        runs OCRmyPDF as a child process, maps its exit codes
â”‚     state.py             persistent JSON state + single-instance lock
â”‚     processor.py         decides what to process; processes one file safely
â”‚     monitor.py           background scan loop, status for CLI/GUI, shutdown
â”‚     cli.py               command-line options
â”‚     gui.py               Tkinter window
â”‚     samples.py           creates scanned-looking test PDFs
â”‚
â”œâ”€ tools\
â”‚     setup_tessdata.py    adds missing OCR language data (Arabic) to .\tessdata
â”‚     install_helper.py    used by install.bat
â”‚     make_test_pdfs.py    creates test PDFs (normal, corrupted, encrypted, large)
â”‚     slow_copy.py         copies a file slowly (to test "still copying" detection)
â”‚
â”œâ”€ Input\                  â†گ put scanned PDFs here (source folder, configurable)
â”œâ”€ Output\                 â†گ searchable PDFs appear here (destination folder, configurable)
â”‚
â”‚  created automatically:
â”œâ”€ .venv\                  Python virtual environment (install.bat)
â”œâ”€ tessdata\               OCR language data, only if setup_tessdata.py had to add languages
â”œâ”€ data\                   state_<id>.json (what has been processed), instance_<id>.lock
â””â”€ logs\                   pdf_ocr.log (+ rotated copies), failed\ (OCRmyPDF output of failed files)
```

---

## 2. Requirements

| Component | Version | Installed by |
|---|---|---|
| Windows | 10 or 11, 64-bit | â€” |
| Python | 3.11, **3.12 (recommended)** or 3.13, 64-bit | install.bat (winget) or python.org |
| ocrmypdf | â‰¥ 16.5, < 17 | pip (requirements.txt) |
| PyMuPDF | â‰¥ 1.24.10, < 1.27 | pip (requirements.txt) |
| **Tesseract OCR** | 5.x (UB Mannheim Windows build) | install.bat (winget) or manually. **pip does not install it.** |
| **Ghostscript** | 10.x, 64-bit | install.bat (official Artifex download) or manually. **pip does not install it.** OCRmyPDF uses it to render pages. |
| Language data | `eng`, `ara` (`osd` for auto-rotation) | Tesseract installer and/or `tools\setup_tessdata.py` |

Why these choices:

* **OCRmyPDF 16.x** is the standard, well-maintained tool for adding an OCR text layer to existing PDFs
  without changing the images. It supports Python 3.10 and newer, and the 16.x command-line options
  this project uses are stable. Versions are capped below 17 so a future major release cannot change
  behaviour without notice.
* **PyMuPDF** is used only to inspect and validate PDFs (page count, encryption, damaged files,
  whether text is present) and to create test files. It is not used for OCR or to rebuild PDFs.
  Note: PyMuPDF is AGPL-licensed, which is fine for internal use.
* **Ghostscript** is required by OCRmyPDF to rasterise pages for Tesseract.
* **pngquant / jbig2enc** are *not* required, because the default `optimize = 1` does not use them.

---

## 3. Quick installation (install.bat)

1. Copy the project folder to `D:\Projects\PDF_OCR` (any location works).
2. Double-click **`install.bat`**. It will:
   * find Python 3.11â€“3.13, or install Python 3.12 for the current user with `winget`;
   * create `.venv` and run `pip install -r requirements.txt`;
   * find Tesseract, or install it with `winget install UB-Mannheim.TesseractOCR`;
   * find Ghostscript, or download the latest official installer from Artifex and install it silently;
   * run `tools\setup_tessdata.py`, which downloads Arabic (and any other missing configured
     language) into `.\tessdata` when Tesseract does not have it yet;
   * create the Input/Output folders and run `verify_install.py`.
3. Accept the Windows (UAC) prompts for Tesseract and Ghostscript if they appear.
4. At the end you should see `Everything is installed correctly.`

If `winget` is missing (older Windows 10 builds), the script tells you which program to install by
hand. Install it as described in section 4, then run `install.bat` again. The script can safely be
run any number of times.

---

## 4. Manual installation

### 4.1 Python

1. Download **Python 3.12 (64-bit)** from <https://www.python.org/downloads/windows/>.
2. Run the installer and tick **"Add python.exe to PATH"**.
3. Open *Command Prompt* in the project folder and run:

```bat
cd /d D:\Projects\PDF_OCR
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 4.2 Installing Tesseract

**Option A, winget:**

```bat
winget install -e --id UB-Mannheim.TesseractOCR
```

**Option B, installer:**

1. Download the latest **tesseract-ocr-w64-setup-5.x.x.exe** from
   <https://github.com/UB-Mannheim/tesseract/wiki>.
2. Run it. On the *Choose Components* page, expand **Additional language data (download)** and
   tick **Arabic**. English is always included. Optionally also expand *Additional script data*
   and tick *Arabic*.
3. Keep the default folder `C:\Program Files\Tesseract-OCR`.

The application finds Tesseract automatically in `C:\Program Files\Tesseract-OCR`, in
`%LOCALAPPDATA%\Programs\Tesseract-OCR`, or on `PATH`. **Adding it to PATH is optional.** If you
install it somewhere else, set it in `config.ini`:

```ini
[paths]
tesseract_dir = E:\Tools\Tesseract-OCR
```

### 4.3 Installing Ghostscript

Ghostscript is **not available from winget**.

**Option A, helper (downloads the latest official 64-bit installer from Artifex's GitHub releases and
installs it silently; accept the administrator prompt):**

```bat
.venv\Scripts\python.exe tools\install_helper.py install-ghostscript
```

**Option B, installer:** download *Ghostscript AGPL Release, 64-bit* from
<https://ghostscript.com/releases/gsdnld.html> and install it with the defaults
(`C:\Program Files\gs\gs10.xx.x`).

The application finds `gswin64c.exe` under `C:\Program Files\gs\*\bin` (newest version first) or
on `PATH`. For a different location:

```ini
[paths]
ghostscript_dir = E:\Tools\gs\gs10.05.1\bin
```

### 4.4 OCR language data (English + Arabic)

Every language in `[ocr] languages` needs a `<code>.traineddata` file. Check what is installed:

```bat
"C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
```

`ara` and `eng` must be in the list. If `ara` is missing, use any one of these:

* **Automatic (recommended, no admin rights):**

  ```bat
  .venv\Scripts\python.exe tools\setup_tessdata.py
  ```

  This creates `D:\Projects\PDF_OCR\tessdata`, copies Tesseract's existing data and its `configs`
  folder into it, and downloads the missing languages from the official repository
  `tesseract-ocr/tessdata_best`. Use `--variant fast` for smaller, faster models or
  `--variant standard` for the same models as the installer. PDF OCR uses `.\tessdata`
  automatically when it exists (the Tesseract environment variable `TESSDATA_PREFIX` is set for
  the OCR process only).
* **Re-run the Tesseract installer** and tick Arabic under *Additional language data*.
* **Copy by hand:** download `ara.traineddata` from
  <https://github.com/tesseract-ocr/tessdata_best> (or `tessdata`) and copy it as administrator
  into `C:\Program Files\Tesseract-OCR\tessdata\`.

If you keep language files in a custom folder, set `tessdata_dir` in `config.ini`. That folder
must contain **all** languages you use plus the `configs` folder from Tesseract's own tessdata.

---

## 5. Verifying the installation

Double-click **`verify.bat`**, or run:

```bat
.venv\Scripts\python.exe verify_install.py
```

It checks Python, ocrmypdf, PyMuPDF, config.ini, the folders, Tesseract and its version, every
configured language, Ghostscript, and `python -m ocrmypdf --version`. It then **OCRs a generated
test scan** and confirms that the text "OCR VERIFICATION" can be extracted from the result.
Expected end of the output:

```
[  OK  ] OCR test passed - recognised text: 'OCR VERIFICATION TEST Hello World 12345 The quick brown fox.'

Everything is installed correctly. Start the application with run.bat or run_gui.bat.
```

Use `verify_install.py --skip-ocr-test` to run only the checks.

---

## 6. Configuration

All settings are in **`config.ini`** (open it with Notepad). The comments in the file explain
every option. The most important ones:

| Section / key | Default | Meaning |
|---|---|---|
| `[folders] source_folder` | `D:\Projects\PDF_OCR\Input` | scanned PDFs (never modified) |
| `[folders] destination_folder` | `D:\Projects\PDF_OCR\Output` | searchable PDFs (created if missing) |
| `[folders] include_subfolders` | `false` | also process subfolders (the structure is mirrored) |
| `[ocr] languages` | `eng+ara` | Tesseract language codes joined with `+` |
| `[ocr] mode` | `skip_text` | pages that already contain text are left untouched |
| `[ocr] rotate_pages` / `deskew` | `false` | auto-rotate / straighten pages (these change the appearance) |
| `[ocr] optimize` | `1` | lossless size optimisation |
| `[ocr] output_type` | `pdf` | `pdfa` for archival PDF/A |
| `[ocr] pdf_renderer` | `sandwich` | Tesseract's renderer; keeps the spaces between Arabic words (`hocr` sometimes glues them together) |
| `[ocr] jobs` | `0` | CPU cores per PDF (0 = all) |
| `[ocr] oversample` | `0` | e.g. `300` for very low-resolution scans |
| `[ocr] file_timeout_minutes` | `120` | maximum OCR time per PDF |
| `[monitor] scan_interval_seconds` | `15` | how often the source folder is scanned |
| `[monitor] stable_seconds` | `10` | how long a new file must stay unchanged before it is processed |
| `[monitor] retry_delay_seconds` / `max_retry_delay_seconds` | `120` / `3600` | back-off after temporary failures |
| `[monitor] min_free_space_mb` | `1024` | free disk space to keep |
| `[paths] tesseract_dir / ghostscript_dir / tessdata_dir` | empty | only needed for non-standard installs |
| `[paths] state_dir` | `data` | where the processing state is stored |
| `[logging] log_dir / log_level` | `logs` / `INFO` | log location and detail |

Relative paths are relative to the folder that contains `config.ini`. Restart the application
after editing the file.

### Changing the source and destination folders

There are three ways. None of them requires changing code.

1. **Edit `config.ini`:**
   ```ini
   [folders]
   source_folder = E:\Scans\Incoming
   destination_folder = \\fileserver\Documents\Searchable
   ```
2. **GUI:** use *Browseâ€¦* next to each folder and click *Start monitoring*. The GUI saves the
   folders, languages and interval into `config.ini`, keeping its comments.
3. **Command line, one run only:**
   `run.bat --source "E:\Scans\Incoming" --dest "E:\Scans\Searchable"`

Rules: the two folders must be different. With `include_subfolders = true` the destination must
not be inside the source. Each (source, destination) pair has its own state file, so switching
folders never mixes up their histories.

---

## 7. Running the application

### Command line (recommended for unattended use)

```bat
run.bat                    REM monitor continuously; Ctrl+C to stop
run.bat --once             REM process everything pending, then exit (exit code 1 if any file failed)
run.bat --retry-failed     REM retry failed files immediately
run.bat --verbose          REM debug logging (shows the exact OCRmyPDF command)
run.bat --help
```

Equivalent without the batch file: `.venv\Scripts\python.exe main.py [options]`.

On start-up the application checks the folders, Tesseract, the languages and Ghostscript. It
refuses to start, with a clear message, if something is missing. It then scans the source folder
straight away and again every `scan_interval_seconds`, until you stop it.

**Stopping:** press **Ctrl+C** or close the window. A file being OCR'd at that moment is cancelled
(the OCR processes are killed). Nothing is marked as done, and that file is processed again on the
next start.

### GUI

Double-click **`run_gui.bat`**. The window lets you:

* choose the source and destination folders (*Browseâ€¦*) and open them in Explorer (*Open*);
* set the OCR languages and scan interval;
* *Start monitoring* / *Stop*;
* see the **Waiting / Processing / Completed / Failed** counts, a batch progress bar and the file
  being processed with its elapsed time;
* see every file with its status and details in the *Files* tab (double-click a row for the full
  message), and the live log with errors in red in the *Log* tab;
* *Retry failed* and *Open log folder*.

### Starting automatically with Windows

Open Task Scheduler and choose *Create Taskâ€¦*:

* *General*: "Run only when user is logged on" (or "whether user is logged on or not" for a
  service-like setup; the account needs access to both folders).
* *Triggers*: "At log on" (or "At startup").
* *Actions*: Program `D:\Projects\PDF_OCR\.venv\Scripts\pythonw.exe`, arguments `main.py`,
  *Start in* `D:\Projects\PDF_OCR`. `pythonw` runs without a window. Everything goes to
  `logs\pdf_ocr.log`.
* *Settings*: untick "Stop the task if it runs longer thanâ€¦".

Ending the task with Task Manager is safe. Unfinished work files are cleaned up on the next start,
and an unfinished file is processed again.

---

## 8. How duplicate prevention and change detection work

**Output name.** The output is `destination_folder\<same relative path and file name>`.

**State file.** `data\state_<id>.json` stores one record per source file:

* the *fingerprint* of the version that was processed: size, modification time and **SHA-256**
  of the content;
* the page count;
* the size and modification time of the output that was written;
* for failures: the error, the number of attempts, the next retry time, and whether the error is
  permanent.

The file is written atomically (temporary file, flush to disk, rename), so a crash or power cut
cannot corrupt it. If it is ever unreadable, it is backed up and the application rebuilds it from
the existing outputs (see "adoption" below).

**On every scan, for each PDF in the source folder:**

| Situation | Result |
|---|---|
| Record = completed, source fingerprint unchanged, output exists with the size/time we wrote | **Skipped**, nothing is opened |
| Output is **missing** | "Output deleted and scheduled for regeneration", then processed again |
| Output was touched or replaced | Opened and checked (valid PDF, same page count). Accepted if valid, otherwise regenerated |
| Source size or time differs | When stable, the SHA-256 is compared. Same content (e.g. only the timestamp changed): record updated, not reprocessed. **Different content: "Source file changed", reprocessed** |
| No record but an output exists (first run, or state lost) | **Adopted** only if the output is newer than the source, opens correctly, has the same page count and page sizes, and contains text. Otherwise regenerated |
| No record, no output | New file, processed once it is ready |
| Record = failed | Permanent error: held until the file changes (or *Retry failed*). Temporary error: retried after 2, 4, 8 â€¦ minutes (max 1 hour) |
| Source deleted | Output kept. The record is kept for 30 days, so if a file with the same name returns, its content is compared with the processed version instead of trusting the old output |

So the state file alone never decides that a file is done. **The output must exist and be valid.**
Deleting an output therefore always regenerates it.

**Files that are still being copied** are not touched until:

1. the size and modification time have stayed the same for `stable_seconds` (checked across scans),
2. the file can be opened (Windows Explorer keeps the file locked while it copies), and
3. the file ends with the PDF end marker `%%EOF`. Some copy tools reserve the full size first,
   so a file without the marker gets up to two more minutes.

Names starting with `~`, `.` or `$`, hidden, system and temporary files, and anything that does not
end in `.pdf` (`.part`, `.crdownload`, `.tmp`) are ignored.

**Processing one file safely:**

1. The free disk space is checked.
2. The source is copied to a private work file in `<destination>\.pdf_ocr_tmp\` (a hidden folder)
   while its SHA-256 is computed. If the source changes during the copy, the attempt is abandoned
   and the file waits again. The original is only ever read.
3. The copy is checked with PyMuPDF: damaged, empty, password-protected or encrypted files fail
   with a clear message.
4. OCRmyPDF runs as a separate process, writing to a temporary output in the same hidden folder.
5. The temporary output is **validated**: it must open, have the same page count and page sizes,
   and its text is counted.
6. Only then is it moved into place with an **atomic rename** (`os.replace`). A reader of the
   destination folder never sees a half-written PDF, and an older valid output is only replaced by
   a complete new one.
7. Only after that is the record marked *completed*.

If the application is stopped or crashes at any point, the state is not updated. The leftover work
files are deleted on the next start and the file is processed again.

**No double processing.** Files are processed one at a time by a single worker. OCRmyPDF itself uses
all CPU cores for the pages of that file. An operating-system file lock
(`data\instance_<id>.lock`) prevents a second copy of the application, CLI or GUI, from working on
the same folder pair. Windows releases the lock automatically if the process dies.

**Why polling instead of the `watchdog` library?** The application scans the folder every few
seconds instead of relying on file-system events:

* events are unreliable on network shares, NAS devices and mapped drives, and can be lost when the
  event buffer overflows or the PC sleeps;
* deleted outputs and missed events would need a periodic full scan anyway;
* a completed copy cannot be recognised from events alone, so stability checks are needed in any
  case;
* a scan only reads file sizes and times, so it costs almost nothing even with thousands of files.

The result is simpler and self-correcting: whatever happens, the next scan sees the true state of
both folders.

---

## 9. Testing scenarios

Preparation: run `install.bat` and `verify.bat`, then start `run.bat` in one window and open a
second *Command Prompt* in `D:\Projects\PDF_OCR` for the commands below. Watch the first window or
`logs\pdf_ocr.log`.

**Test 1: three scanned PDFs are converted**

```bat
.venv\Scripts\python.exe tools\make_test_pdfs.py
```

This creates `sample_scan_1.pdf` â€¦ `sample_scan_3.pdf` in the Input folder (image-only, 2 pages
each). You can also use your own scans. Expected log, after about `stable_seconds`:
`STARTED â€¦ COMPLETED sample_scan_1.pdf â€” 2 page(s), â€¦ characters of text` for all three, and
`Output\` contains three files with the same names. The Input files are unchanged.

**Test 2: restart, completed files are skipped**

Stop with Ctrl+C and start `run.bat` again. Expected: `SKIPPED    sample_scan_N.pdf â€” processing
skipped because a valid output already exists` for each file, and no `STARTED` lines.

**Test 3: only the new fourth PDF is processed**

```bat
.venv\Scripts\python.exe tools\make_test_pdfs.py --start 4 --count 1
```

Expected: `WAITING sample_scan_4.pdf â€” new PDF detected â€¦`, then `STARTED`/`COMPLETED` for that
file only.

**Test 4: a deleted output is regenerated**

```bat
del Output\sample_scan_2.pdf
```

Expected within one scan interval: `REGENERATE sample_scan_2.pdf â€” output PDF was deleted from the
destination folder; scheduled for regeneration`, then `STARTED` and `COMPLETED`.

**Test 5: a modified source is reprocessed**

Replace a source with different content but the same name:

```bat
copy /y Input\sample_scan_4.pdf Input\sample_scan_1.pdf
```

Expected: `REPROCESS sample_scan_1.pdf â€” source file changed since it was processed; scheduled for
reprocessing`, then `COMPLETED`. Searching the new `Output\sample_scan_1.pdf` now finds `ZEBRA004`.
Just touching the file without changing its content is detected by the hash and does **not**
trigger reprocessing.

**Test 6: a corrupted PDF is reported, the others still work**

```bat
.venv\Scripts\python.exe tools\make_test_pdfs.py --corrupted
.venv\Scripts\python.exe tools\make_test_pdfs.py --start 5 --count 1
```

Expected: `FAILED corrupted_sample.pdf â€” source PDF cannot be opened as a PDF (damaged or not a PDF
file) â€¦ will be retried when the source file changes`, and `sample_scan_5.pdf` still completes.
The failure is logged once and not retried every scan. `--encrypted` creates a password-protected
PDF, which is reported the same way.

**Test 7: a large file is processed only after the copy has finished**

```bat
mkdir Staging
.venv\Scripts\python.exe tools\make_test_pdfs.py --large 150 --out Staging
```

Then do either of the following:

* copy `Staging\large_sample.pdf` into `Input` with **Explorer** and watch the log while the copy
  runs: `WAITING large_sample.pdf â€” â€¦ file is locked by another program (still being copiedâ€¦)` or
  `waiting until the file stops changing`; or
* simulate a slow network copy:
  `.venv\Scripts\python.exe tools\slow_copy.py Staging\large_sample.pdf Input --chunk-kb 512 --delay 1`

Expected: no `STARTED` line until the copy has finished and the file has been unchanged for
`stable_seconds`. Then the file is processed once.

**Test 8: the text is searchable and selectable**

Open `Output\sample_scan_3.pdf` in Adobe Acrobat Reader, Edge or SumatraPDF. Press **Ctrl+F**,
search for `ZEBRA003` or `quick brown fox`: the hit is highlighted. Drag over the text with the
selection tool and copy-paste it into Notepad. For Arabic, use one of your own Arabic scans and
search for an Arabic word. You can also check from the command line:

```bat
.venv\Scripts\python.exe -c "import pymupdf,sys; d=pymupdf.open(sys.argv[1]); print(d[0].get_text())" Output\sample_scan_3.pdf
```

For **Arabic**, use pdfminer (installed with OCRmyPDF) instead. PyMuPDF does not reorder right-to-left
text, so it prints Arabic words with their letters reversed, even though the PDF is correct:

```bat
.venv\Scripts\python.exe -c "import sys; from pdfminer.high_level import extract_text; print(extract_text(sys.argv[1], page_numbers=[0]))" "Output\your arabic file.pdf"
```

---

## 10. Troubleshooting

| Problem / message | Cause and fix |
|---|---|
| `Python was not found; run without arguments to install from the Microsoft Store` | Only the Store shortcut exists. Run `install.bat` (installs Python 3.12) or install it from python.org. |
| `Tesseract OCR was not found` | Install it (section 4.2) or set `[paths] tesseract_dir`. Check with `"C:\Program Files\Tesseract-OCR\tesseract.exe" --version`. |
| `Tesseract language data is missing for: ara` | Run `.venv\Scripts\python.exe tools\setup_tessdata.py` (section 4.4). |
| `Ghostscript was not found` | Install it (section 4.3) or set `[paths] ghostscript_dir` to the folder containing `gswin64c.exe`. |
| `Source folder does not exist` | Create the folder or correct `source_folder` in `config.ini`. |
| `Destination folder is not writable` | Choose another folder or fix its permissions. A running task must use an account that can write there. |
| `Another PDF OCR process is already working on â€¦` | Another copy is running (check Task Manager for `python.exe`/`pythonw.exe`). Stop it first. |
| `FAILED x.pdf â€” â€¦ password-protected` / `â€¦ encrypted` | OCRmyPDF cannot process encrypted PDFs. Save an unprotected copy (e.g. *Print â†’ Microsoft Print to PDF*) and put it in the source folder. |
| `FAILED x.pdf â€” â€¦ damaged or not a PDF file` | The file is broken. Replace it; the new version is processed automatically. |
| `the PDF already contains text` | Only with `mode` other than `skip_text`. Use `mode = skip_text` (default) or `redo_ocr`. |
| `insufficient disk space â€¦` | Free space on the destination drive or the `%TEMP%` drive. Retried automatically. |
| `could not save the output PDF â€¦ Close it if it is open` | The old output is open in a PDF viewer that locks it. Close the viewer; it is retried automatically. |
| `OCR did not finish within 120 minutes` | Very large file or slow PC. Increase `file_timeout_minutes`. |
| File stays in **Waiting: file is locked by another program** | Another program (copy job, scanner software, antivirus) keeps it open. It continues automatically when released. |
| Output has no or poor text | Check `languages` (the wrong language gives poor results). Try `oversample = 300` for low-resolution scans and `rotate_pages = true` for sideways pages. The log line `â€¦ characters of text` shows how much was found. |
| Arabic words are glued together when searching or copying | Use `pdf_renderer = sandwich` (the default) and reprocess the file by deleting its output. |
| Arabic text looks reversed when extracted with a script | PyMuPDF and some simple tools do not reorder right-to-left text. Check in Edge/Acrobat or with pdfminer (section 9, Test 8). |
| Need more detail about a failure | `logs\pdf_ocr.log`, and the full OCRmyPDF output of each failed file in `logs\failed\`. Run `run.bat --verbose` to see the exact command. |
| Want to reprocess everything | Stop the application, delete the outputs (or the `data\state_*.json` file *and* the outputs), and start again. |
| `pip install` fails building a package | You are probably on an unsupported Python version (e.g. 32-bit or very new). Use 64-bit Python 3.12. |

---

## 11. Log messages

Each line has a timestamp and a level. The per-file lines are:

| Prefix | Meaning |
|---|---|
| `WAITING` | file found and waiting for processing (with the reason: new, still copying, locked, â€¦) |
| `REPROCESS` | the source changed; scheduled for reprocessing |
| `REGENERATE` | the output was deleted or is invalid; scheduled for regeneration |
| `RETRY` | retrying after an earlier temporary failure |
| `STARTED` | processing started |
| `COMPLETED` | output written and validated (pages, characters of text, duration) |
| `SKIPPED` | processing skipped because a valid output already exists |
| `FAILED` | the error and when it will be retried |
| `CANCELLED` | stopped during OCR; it will be processed again on the next start |
| `REMOVED` | the source file was deleted (the output is kept; the state entry is purged after 30 days) |

A file's status is logged when it changes, not on every scan, so the log stays readable during
long unattended runs. The log file rotates at 10 MB and keeps 5 old files.
