"""Backward-compatible script entry point for the pdf2epub package."""

# ruff: noqa: E402, I001

import sys
from pathlib import Path

source_directory = Path(__file__).resolve().parent / "src"
if source_directory.is_dir():
    sys.path.insert(0, str(source_directory))

from pdf2epub.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
