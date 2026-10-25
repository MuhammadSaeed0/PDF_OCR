# PDF OCR — scanned PDFs → searchable PDFs (tuned for Arabic)

PDF OCR watches a folder of scanned (image-only) PDFs and saves a searchable copy of each one,
with the same file name, in another folder. It uses the [Tesseract](https://github.com/tesseract-ocr/tesseract)
OCR engine with its most accurate Arabic model, plus image clean-up made for Arabic school books
and photographed pages. The language of each book (Arabic or English) is detected automatically.

* Every page gets an invisible text layer, so you can search, select and copy the text in any PDF
  reader. Arabic is stored in correct **right-to-left** order, so phrases can be searched and
  copied text reads normally.
* The scanned images are never re-encoded. Crooked pages are straightened by a rotation
  instruction only, so the file size stays the same.
* The original PDFs are never modified.
* New PDFs are picked up automatically. A file is never processed twice. Deleted or damaged
  outputs, changed source files and improved OCR settings are detected and the affected files are
  processed again.
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
8. [How the OCR works (Arabic quality)](#8-how-the-ocr-works-arabic-quality)
9. [How duplicate prevention and change detection work](#9-how-duplicate-prevention-and-change-detection-work)
10. [Testing scenarios](#10-testing-scenarios)
11. [Troubleshooting](#11-troubleshooting)
12. [Log messages](#12-log-messages)

---

## 1. Project structure

```
D:\Projects\PDF_OCR\
│  config.ini              ← all settings (folders, languages, intervals, ...)
│  requirements.txt        ← Python packages
│  install.bat             ← one-click installer
│  run.bat                 ← start monitoring (console)
│  run_gui.bat             ← start the graphical window
│  verify.bat              ← check the installation + real OCR test
│  main.py                 ← entry point (CLI and --gui)
│  verify_install.py       ← the installation check run by verify.bat
│  README.md
│
├─ pdf_ocr\                ← application package
│     __init__.py          version
│     config.py            reads/validates config.ini, updates it from the GUI
│     logging_setup.py     log file (rotating) + console
│     dependencies.py      finds Tesseract and its language data, start-up checks
│     pdf_utils.py         PDF inspection & validation (PyMuPDF), hashing, safe copy
│     ocr_engine.py        page clean-up, deskew, parallel Tesseract, text layer, language detection
│     state.py             persistent JSON state + single-instance lock
│     processor.py         decides what to process; processes one file safely
│     monitor.py           background scan loop, status for CLI/GUI, shutdown
│     cli.py               command-line options
│     gui.py               Tkinter window
│     samples.py           creates scanned-looking test PDFs
│
├─ tools\
│     setup_tessdata.py    adds missing OCR language data (Arabic, English) to .\tessdata
│     install_helper.py    used by install.bat
│     make_test_pdfs.py    creates test PDFs (normal, corrupted, encrypted, large)
│     slow_copy.py         copies a file slowly (to test "still copying" detection)
│
├─ Input\                  ← put scanned PDFs here (source folder, configurable)
├─ Output\                 ← searchable PDFs appear here (destination folder, configurable)
│
│  created automatically:
├─ .venv\                  Python virtual environment (install.bat)
├─ tessdata\               OCR language data, if setup_tessdata.py had to add languages
├─ data\                   state_<id>.json (what has been processed), instance_<id>.lock
└─ logs\                   pdf_ocr.log (+ rotated copies), failed\ (per-page OCR log of problem files)
```

---

## 2. Requirements

| Component | Version | Installed by |
|---|---|---|
| Windows | 10 or 11, 64-bit | — |
| Python | 3.11, **3.12 (recommended)** or 3.13, 64-bit | install.bat (winget) or python.org |
| PyMuPDF | ≥ 1.24.10, < 1.27 | pip (requirements.txt) |
| numpy, Pillow | numpy ≥ 1.26, Pillow ≥ 10 | pip (requirements.txt) |
| **Tesseract OCR** | 5.x (UB Mannheim Windows build) | install.bat (winget) or manually. **pip does not install it.** |
| Language data | `ara` and `eng` from `tessdata_best` | Tesseract installer and/or `tools\setup_tessdata.py` |

Why these choices:

* **Tesseract 5 with the `tessdata_best` Arabic model** gave the best Arabic results on a normal PC
  in tests on real Egyptian secondary-school books. PaddleOCR's Arabic model was tested as well: it
  glued words together and garbled coloured text. Large vision models (Surya, PaddleOCR-VL) need
  a strong graphics card; on a laptop processor they take minutes per page.
* **PyMuPDF** opens and renders the PDFs, adds the text layer and validates the results.
  It is AGPL-licensed, which is fine for internal use.
* **numpy + Pillow** clean up the page images before OCR (section 8).
* Earlier versions used OCRmyPDF and Ghostscript. They are **no longer needed**: OCRmyPDF could not
  clean up coloured print or straighten pages without re-encoding the scans, and it misread the
  page resolution of many scanned books, which left pages without text.

---

## 3. Quick installation (install.bat)

1. Copy the project folder to `D:\Projects\PDF_OCR` (any location works).
2. Double-click **`install.bat`**. It will:
   * find Python 3.11–3.13, or install Python 3.12 for the current user with `winget`;
   * create `.venv` and run `pip install -r requirements.txt`;
   * find Tesseract, or install it with `winget install UB-Mannheim.TesseractOCR`;
   * run `tools\setup_tessdata.py`, which downloads Arabic and English (if missing) from
     `tessdata_best` into `.\tessdata`;
   * create the Input/Output folders and run `verify_install.py`.
3. Accept the Windows (UAC) prompt for Tesseract if it appears.
4. At the end you should see `Everything is installed correctly.`

If `winget` is missing (older Windows 10 builds), the script tells you what to install by hand.
Install it as described in section 4, then run `install.bat` again. The script can safely be run
any number of times.

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
2. Run it and keep the default folder `C:\Program Files\Tesseract-OCR`.

The application finds Tesseract automatically in `C:\Program Files\Tesseract-OCR`, in
`%LOCALAPPDATA%\Programs\Tesseract-OCR`, or on `PATH`. **Adding it to PATH is optional.** If you
install it somewhere else, set it in `config.ini`:

```ini
[paths]
tesseract_dir = E:\Tools\Tesseract-OCR
```

### 4.3 OCR language data (Arabic + English)

With `languages = auto` (the default) both `ara` and `eng` are needed. Run:

```bat
.venv\Scripts\python.exe tools\setup_tessdata.py
```

This creates `D:\Projects\PDF_OCR\tessdata`, copies Tesseract's existing data (including its
`configs` folder and `pdf.ttf`, which the text layer needs) into it, and downloads the missing
languages from the official repository `tesseract-ocr/tessdata_best`, which has the most accurate
models. PDF OCR uses `.\tessdata` automatically when it exists. No administrator rights are needed.

To check what is installed:

```bat
"C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
```

If you keep language files in a custom folder, set `tessdata_dir` in `config.ini`. That folder
must contain **all** languages you use, plus the `configs` folder and `pdf.ttf` from Tesseract's
own `tessdata` folder.

---

## 5. Verifying the installation

Double-click **`verify.bat`**, or run:

```bat
.venv\Scripts\python.exe verify_install.py
```

It checks Python, PyMuPDF, numpy, Pillow, config.ini, the folders, Tesseract and its version, and
every needed language. It then **OCRs a generated test scan** with the real engine (including the
automatic language detection) and confirms that the text "OCR VERIFICATION" can be extracted from
the result. Expected end of the output:

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
| `[ocr] languages` | `auto` | `auto` detects Arabic or English per book. Or fixed Tesseract codes: `ara`, `eng+ara`, `ara+eng`, … |
| `[ocr] resolution` | `300` | resolution used to read the pages (each page is enlarged to A4 at this resolution) |
| `[ocr] page_segmentation_mode` | `3` | Tesseract layout analysis (3 = automatic) |
| `[ocr] skip_pages_with_text` | `true` | pages that already contain text are left as they are |
| `[ocr] straighten_pages` | `true` | straighten crooked scans (needed for correct reading order on those pages) |
| `[ocr] jobs` | `0` | pages OCR'd at the same time (0 = one per CPU thread) |
| `[ocr] tesseract_timeout` | `300` | maximum seconds per page |
| `[ocr] file_timeout_minutes` | `360` | maximum OCR time per PDF |
| `[monitor] scan_interval_seconds` | `15` | how often the source folder is scanned |
| `[monitor] stable_seconds` | `10` | how long a new file must stay unchanged before it is processed |
| `[monitor] retry_delay_seconds` / `max_retry_delay_seconds` | `120` / `3600` | back-off after temporary failures |
| `[monitor] min_free_space_mb` | `1024` | free disk space to keep |
| `[paths] tesseract_dir / tessdata_dir` | empty | only needed for non-standard installs |
| `[paths] state_dir` | `data` | where the processing state is stored |
| `[logging] log_dir / log_level` | `logs` / `INFO` | log location and detail |

Relative paths are relative to the folder that contains `config.ini`. Restart the application
after editing the file. **Changing an `[ocr]` setting makes every PDF be processed again** with the
new settings (see section 9).

### Changing the source and destination folders

There are three ways. None of them requires changing code.

1. **Edit `config.ini`:**
   ```ini
   [folders]
   source_folder = E:\Scans\Incoming
   destination_folder = \\fileserver\Documents\Searchable
   ```
2. **GUI:** use *Browse…* next to each folder and click *Start monitoring*. The GUI saves the
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
run.bat --verbose          REM debug logging
run.bat --help
```

Equivalent without the batch file: `.venv\Scripts\python.exe main.py [options]`.

On start-up the application checks the folders, Tesseract and the languages. It refuses to start,
with a clear message, if something is missing. It then scans the source folder straight away and
again every `scan_interval_seconds`, until you stop it. During a long book it logs a `PROGRESS`
line every 10 % of the pages.

**Speed:** about 4–6 seconds per page on a two-core laptop (pages are OCR'd in parallel), so a
350-page book takes roughly 30 minutes.

**Stopping:** press **Ctrl+C** or close the window. A file being OCR'd at that moment is cancelled
(the Tesseract processes are killed). Nothing is marked as done, and that file is processed again
on the next start.

### GUI

Double-click **`run_gui.bat`**. The window lets you:

* choose the source and destination folders (*Browse…*) and open them in Explorer (*Open*);
* set the OCR languages (`auto` by default) and scan interval;
* *Start monitoring* / *Stop*;
* see the **Waiting / Processing / Completed / Failed** counts, a progress bar with the page being
  OCR'd ("page 120 of 344") and the elapsed time;
* see every file with its status and details in the *Files* tab (double-click a row for the full
  message), and the live log with errors in red in the *Log* tab;
* *Retry failed* and *Open log folder*.

### Starting automatically with Windows

Open Task Scheduler and choose *Create Task…*:

* *General*: "Run only when user is logged on" (or "whether user is logged on or not" for a
  service-like setup; the account needs access to both folders).
* *Triggers*: "At log on" (or "At startup").
* *Actions*: Program `D:\Projects\PDF_OCR\.venv\Scripts\pythonw.exe`, arguments `main.py`,
  *Start in* `D:\Projects\PDF_OCR`. `pythonw` runs without a window. Everything goes to
  `logs\pdf_ocr.log`.
* *Settings*: untick "Stop the task if it runs longer than…".

Ending the task with Task Manager is safe. Unfinished work files are cleaned up on the next start,
and an unfinished file is processed again.

---

## 8. How the OCR works (Arabic quality)

For every page that has no text yet (`ocr_engine.py`):

1. **Normalised size.** Many scanned books declare huge pages at 96 dpi (for example 43 × 62 cm).
   Tesseract then sees tiny letters and reports "Empty page", which is why some pages used to stay
   without text. Every page is now rendered so that its long side matches A4 at `resolution` dpi,
   whatever size the PDF declares.
2. **Coloured print.** School books print many words in pink, blue or green. In a normal grey
   conversion they become light grey and are misread. The engine keeps the *darkest colour
   channel*, so coloured ink becomes as dark as black. It then divides by the local paper colour,
   so tinted panels and coloured backgrounds stay light, and stretches the contrast.
3. **Straightening.** The skew of the text lines is measured with a projection profile (up to
   ±15°, using only dark ink so that light diagonal watermarks are ignored) and the image is
   straightened before OCR. Photographed pages tilted by 5–10° went from almost no text to
   complete pages. Because the estimate can still be fooled by watermarks, frames or diagrams,
   a page estimated at 1.5° or more is read a second time as it is, and the reading with more
   words wins.
4. **Recognition.** Tesseract reads the page with the `tessdata_best` model. With `languages = auto`
   the engine first reads three sample pages and counts Arabic and Latin words:
   * Arabic books are read with **Arabic only** (`ara`). With English enabled as well, Tesseract
     turned hard Arabic words into Latin garbage (`KBE) F patna`), and that garbage disappears.
   * English books (35 % or more Latin words) are read with `eng+ara`.
5. **Right-to-left text layer.** Tesseract's own PDF renderer writes the invisible text, storing
   Arabic in logical (reading) order. For straightened pages, the page itself is straightened by
   the same angle: a rotation instruction is placed around the original page content, and the
   scanned image bytes are untouched. The text layer then lies horizontally exactly on the printed
   words. This matters: Edge and Chrome (PDFium) cut a *tilted* Arabic text line into pieces and
   read them left to right, which scrambles the word order.

Tested on real pages: searching for whole phrases such as «النصوص المحررة على القصة القصيرة» or
«مفهوم اسم المفعول» finds them in Edge/Chrome, and copied text reads right to left in the correct
order.

Limits: decorative titles, white text on coloured bars, handwriting and very blurred photos are
still read poorly. Brackets around Arabic words may come out mirrored in copied text, for example
`)عبده(`. This is a known Tesseract issue.

---

## 9. How duplicate prevention and change detection work

**Output name.** The output is `destination_folder\<same relative path and file name>`.

**State file.** `data\state_<id>.json` stores one record per source file:

* the *fingerprint* of the version that was processed: size, modification time and **SHA-256**
  of the content;
* the page count;
* the size and modification time of the output that was written;
* the **OCR signature**: engine version and OCR settings (languages, resolution, …);
* for failures: the error, the number of attempts, the next retry time, and whether the error is
  permanent.

The file is written atomically (temporary file, flush to disk, rename), so a crash or power cut
cannot corrupt it. If it is ever unreadable, it is backed up and the application rebuilds it from
the existing outputs (see "adoption" below).

**On every scan, for each PDF in the source folder:**

| Situation | Result |
|---|---|
| Record = completed, source fingerprint unchanged, output exists with the size/time we wrote, same OCR signature | **Skipped**, nothing is opened |
| Output is **missing** | "Output deleted and scheduled for regeneration", then processed again |
| Output was touched or replaced | Opened and checked (valid PDF, same page count). Accepted if valid, otherwise regenerated |
| OCR engine improved or `[ocr]` settings changed | "Output was made with older OCR settings", processed again |
| Source size or time differs | When stable, the SHA-256 is compared. Same content (e.g. only the timestamp changed): record updated, not reprocessed. **Different content: "Source file changed", reprocessed** |
| No record but an output exists (first run, or state lost) | **Adopted** only if the output is newer than the source, opens correctly, has the same page count and page sizes, and contains text. Otherwise regenerated |
| No record, no output | New file, processed once it is ready |
| Record = failed | Permanent error: held until the file changes (or *Retry failed*). Temporary error: retried after 2, 4, 8 … minutes (max 1 hour) |
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
3. The copy is checked with PyMuPDF: damaged, empty or password-protected files fail with a clear
   message.
4. The pages are OCR'd (section 8) into a temporary output in the same hidden folder.
5. The temporary output is **validated**: it must open, have the same page count and page sizes,
   and its text is counted.
6. Only then is it moved into place with an **atomic rename** (`os.replace`). A reader of the
   destination folder never sees a half-written PDF, and an older valid output is only replaced by
   a complete new one.
7. Only after that is the record marked *completed*.

If the application is stopped or crashes at any point, the state is not updated. The leftover work
files are deleted on the next start and the file is processed again.

**No double processing.** Files are processed one at a time by a single worker, which OCRs the
pages of that file in parallel. An operating-system file lock (`data\instance_<id>.lock`) prevents a
second copy of the application, CLI or GUI, from working on the same folder pair. Windows releases
the lock automatically if the process dies.

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

## 10. Testing scenarios

Preparation: run `install.bat` and `verify.bat`, then start `run.bat` in one window and open a
second *Command Prompt* in `D:\Projects\PDF_OCR` for the commands below. Watch the first window or
`logs\pdf_ocr.log`.

**Test 1: three scanned PDFs are converted**

```bat
.venv\Scripts\python.exe tools\make_test_pdfs.py
```

This creates `sample_scan_1.pdf` … `sample_scan_3.pdf` in the Input folder (image-only, 2 pages
each). You can also use your own scans. Expected log, after about `stable_seconds`:
`STARTED … COMPLETED sample_scan_1.pdf — 2 page(s), … characters of text` for all three, and
`Output\` contains three files with the same names. The Input files are unchanged.

**Test 2: restart, completed files are skipped**

Stop with Ctrl+C and start `run.bat` again. Expected: `SKIPPED    sample_scan_N.pdf — processing
skipped because a valid output already exists` for each file, and no `STARTED` lines.

**Test 3: only the new fourth PDF is processed**

```bat
.venv\Scripts\python.exe tools\make_test_pdfs.py --start 4 --count 1
```

Expected: `WAITING sample_scan_4.pdf — new PDF detected …`, then `STARTED`/`COMPLETED` for that
file only.

**Test 4: a deleted output is regenerated**

```bat
del Output\sample_scan_2.pdf
```

Expected within one scan interval: `REGENERATE sample_scan_2.pdf — output PDF was deleted from the
destination folder; scheduled for regeneration`, then `STARTED` and `COMPLETED`.

**Test 5: a modified source is reprocessed**

Replace a source with different content but the same name:

```bat
copy /y Input\sample_scan_4.pdf Input\sample_scan_1.pdf
```

Expected: `REPROCESS sample_scan_1.pdf — source file changed since it was processed; scheduled for
reprocessing`, then `COMPLETED`. Searching the new `Output\sample_scan_1.pdf` now finds `ZEBRA004`.
Just touching the file without changing its content is detected by the hash and does **not**
trigger reprocessing.

**Test 6: a corrupted PDF is reported, the others still work**

```bat
.venv\Scripts\python.exe tools\make_test_pdfs.py --corrupted
.venv\Scripts\python.exe tools\make_test_pdfs.py --start 5 --count 1
```

Expected: `FAILED corrupted_sample.pdf — source PDF cannot be opened as a PDF (damaged or not a PDF
file) — will be retried when the source file changes`, and `sample_scan_5.pdf` still completes.
The failure is logged once and not retried every scan. `--encrypted` creates a password-protected
PDF, which is reported the same way.

**Test 7: a large file is processed only after the copy has finished**

```bat
mkdir Staging
.venv\Scripts\python.exe tools\make_test_pdfs.py --large 150 --out Staging
```

Then do either of the following:

* copy `Staging\large_sample.pdf` into `Input` with **Explorer** and watch the log while the copy
  runs: `WAITING large_sample.pdf — … file is locked by another program (still being copied…)` or
  `waiting until the file stops changing`; or
* simulate a slow network copy:
  `.venv\Scripts\python.exe tools\slow_copy.py Staging\large_sample.pdf Input --chunk-kb 512 --delay 1`

Expected: no `STARTED` line until the copy has finished and the file has been unchanged for
`stable_seconds`. Then the file is processed once.

**Test 8: the text is searchable and selectable (including Arabic)**

Open an output PDF in **Edge, Chrome or Adobe Acrobat Reader**. Press **Ctrl+F** and search for a
word or a whole phrase. For the samples, use `ZEBRA003`. For an Arabic book, use a phrase from the
page, for example «مفهوم اسم المفعول». The hit is highlighted on the printed words. Select a few
lines, copy them and paste into Notepad or Word: Arabic reads right to left, in the correct word
order.

Note: `pymupdf`'s `get_text()` in a Python script prints Arabic letters in visual (reversed)
order even when the PDF is correct. Judge Arabic in a PDF reader.

---

## 11. Troubleshooting

| Problem / message | Cause and fix |
|---|---|
| `Python was not found; run without arguments to install from the Microsoft Store` | Only the Store shortcut exists. Run `install.bat` (installs Python 3.12) or install it from python.org. |
| `Tesseract OCR was not found` | Install it (section 4.2) or set `[paths] tesseract_dir`. Check with `"C:\Program Files\Tesseract-OCR\tesseract.exe" --version`. |
| `Tesseract language data is missing for: ara` (or `eng`) | Run `.venv\Scripts\python.exe tools\setup_tessdata.py` (section 4.3). |
| `Source folder does not exist` | Create the folder or correct `source_folder` in `config.ini`. |
| `Destination folder is not writable` | Choose another folder or fix its permissions. A running task must use an account that can write there. |
| `Another PDF OCR process is already working on …` | Another copy is running (check Task Manager for `python.exe`/`pythonw.exe`). Stop it first. |
| `FAILED x.pdf — … password-protected` | Save a copy without the password and put it in the source folder. (PDFs with only copy/print restrictions are processed normally.) |
| `FAILED x.pdf — … damaged or not a PDF file` | The file is broken. Replace it; the new version is processed automatically. |
| `insufficient disk space …` | Free space on the destination drive or the `%TEMP%` drive. Retried automatically. |
| `could not save the output PDF … Close it if it is open` | The old output is open in a PDF viewer that locks it. Close the viewer; it is retried automatically. |
| `OCR did not finish within 360 minutes` | Very large file or slow PC. Increase `file_timeout_minutes`. |
| `COMPLETED … (OCR failed on page(s) …)` | Those pages timed out or Tesseract failed on them. Details in `logs\failed\<file>.ocr.log`. Increase `tesseract_timeout` if they timed out. |
| File stays in **Waiting: file is locked by another program** | Another program (copy job, scanner software, antivirus) keeps it open. It continues automatically when released. |
| Arabic book shows Latin letters mixed into the text | Use `languages = auto` (default) or `ara`. `ara+eng` keeps English terms but lets hard Arabic words turn into Latin letters. |
| English terms (DNA, …) missing in an Arabic science book | `auto` reads Arabic books with Arabic only. Use `languages = ara+eng` if the English terms matter more than a clean Arabic text. |
| Some pages still have little or no text | Usually decorative covers, blank "notes" pages, white-on-colour headings, handwriting or very blurred photos. Try `resolution = 400` for very small print. |
| Arabic text looks reversed when extracted with a script | PyMuPDF and simple tools do not reorder right-to-left text. Check in Edge/Chrome/Acrobat (section 10, Test 8). |
| Want to reprocess everything | Change nothing and delete the outputs, or stop the application and delete `data\state_*.json` *and* the outputs. |
| `pip install` fails building a package | You are probably on an unsupported Python version (e.g. 32-bit or very new). Use 64-bit Python 3.12. |

---

## 12. Log messages

Each line has a timestamp and a level. The per-file lines are:

| Prefix | Meaning |
|---|---|
| `WAITING` | file found and waiting for processing (with the reason: new, still copying, locked, …) |
| `REPROCESS` | the source changed, or the OCR engine/settings improved; scheduled for reprocessing |
| `REGENERATE` | the output was deleted or is invalid; scheduled for regeneration |
| `RETRY` | retrying after an earlier temporary failure |
| `STARTED` | processing started |
| `PROGRESS` | pages done so far in a long file |
| `COMPLETED` | output written and validated (pages, characters of text, duration, detected language) |
| `SKIPPED` | processing skipped because a valid output already exists |
| `FAILED` | the error and when it will be retried |
| `CANCELLED` | stopped during OCR; it will be processed again on the next start |
| `REMOVED` | the source file was deleted (the output is kept; the state entry is purged after 30 days) |

A file's status is logged when it changes, not on every scan, so the log stays readable during
long unattended runs. The log file rotates at 10 MB and keeps 5 old files.
