# PDF to EPUB Structural Converter

A Python 3.9+ command-line tool and library that converts text-based PDF documents into
reflowable EPUB 3.3 books. It uses conservative layout-aware parsing to reconstruct
paragraphs, identify chapter headings and subtitles, and remove repeated running headers,
footers, and page numbers.

## Features

- Produces EPUB 3.3 with an XHTML navigation document and NCX fallback.
- Splits detected chapters into separate XHTML resources.
- Uses PDF font, position, spacing, outline, and text evidence when available.
- Extracts title and author metadata, with explicit command-line overrides.
- Writes beside the input by default and never overwrites unless requested.
- Validates manifest, spine, navigation resources, and anchors before writing.
- Writes atomically so failed conversions do not leave partial output files.

## Installation

Install the package and its `pdf2epub` command:

```bash
python3 -m pip install .
```

For development tools:

```bash
python3 -m pip install -e ".[dev]"
```

The runtime dependency is `pypdf>=6,<7`.

## Usage

```bash
pdf2epub INPUT [-o OUTPUT] [--title TITLE] [--author AUTHOR]
               [--language TAG] [--overwrite] [-v]
```

Direct script execution remains supported from a source checkout:

```bash
python3 convert_pdf.py your_document.pdf
```

Examples:

```bash
pdf2epub book.pdf
pdf2epub book.pdf --title "A Better Title" --author "Ada Example" --language en
pdf2epub book.pdf -o exports/book.epub --overwrite --verbose
```

Metadata precedence is command-line override, then PDF metadata, then a fallback:

- Title falls back to the input filename.
- Author is omitted when unavailable.
- Language falls back to the BCP-47 `und` (undetermined) tag.

## Output

The script will generate an `.epub` file in the same directory as the input PDF.

- If `book.pdf` is passed, it outputs `book.epub`.
- If `book.epub` already exists, it outputs `book-1.epub`.
- Additional collisions use `book-2.epub`, `book-3.epub`, and so on.
- `--overwrite` replaces the exact requested path atomically.
- A suffix-less `--output` path automatically receives `.epub`.

## Python API

```python
from pdf2epub import ConversionOptions, convert_pdf

result = convert_pdf(
    "book.pdf",
    options=ConversionOptions(language="en"),
)
print(result.output_path)
```

`ConversionResult` includes resolved metadata, publication UUID, chapter count, and
non-fatal extraction warnings. Expected failures derive from `Pdf2EpubError`.

## Limitations

PDF is a presentation format without reliable paragraph or heading semantics, so structural
detection is necessarily heuristic.

- Image-only and scanned PDFs require OCR before conversion. This tool does not run OCR.
- Password-protected PDFs are rejected.
- Images, tables, hyperlinks, footnotes, columns, and detailed source formatting are not
  preserved.
- Unusual transformations or malformed font coordinates may trigger text-only fallback.
- Very large uncompressed PDF content streams can require substantial memory in `pypdf`.

## Development

```bash
ruff check .
ruff format --check .
pytest
```

Continuous integration runs on Python 3.9, 3.12, and 3.14 and validates a representative
publication with EPUBCheck 5.3.0.
