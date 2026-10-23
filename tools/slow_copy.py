"""Copies a file slowly, to test that PDFs still being copied are not processed too early.

    .venv\\Scripts\\python tools\\slow_copy.py SOURCE_FILE DESTINATION_FOLDER [--chunk-kb 256] [--delay 1]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination_folder", type=Path)
    parser.add_argument("--chunk-kb", type=int, default=256, help="bytes written per step, in KB (default 256)")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds to pause after each step (default 1)")
    args = parser.parse_args()

    target = args.destination_folder / args.source.name
    total = args.source.stat().st_size
    written = 0
    print(f"Copying {args.source} -> {target} ({total // 1024} KB) slowly...")
    with open(args.source, "rb") as fin, open(target, "wb") as fout:
        while True:
            chunk = fin.read(args.chunk_kb * 1024)
            if not chunk:
                break
            fout.write(chunk)
            fout.flush()
            written += len(chunk)
            print(f"\r  {written * 100 // max(total, 1):3d}%", end="", flush=True)
            time.sleep(args.delay)
    print("\nCopy finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
