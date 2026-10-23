"""PDF OCR - start here.

    python main.py                 monitor the folders from config.ini (Ctrl+C to stop)
    python main.py --gui           graphical interface
    python main.py --once          process what is pending, then exit
    python main.py --help          all options
"""

import sys

from pdf_ocr.cli import main

if __name__ == "__main__":
    sys.exit(main())
