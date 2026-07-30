# PDF to EPUB Structural Converter

A Python command-line tool that intelligently converts PDF documents into properly formatted EPUB books.

Instead of blindly extracting text and breaking paragraphs at every visual line (a common issue with raw PDF scraping), this script uses heuristic parsing to detect natural paragraph breaks, headings, and subtitles.

## Features

- **Heuristic Paragraph Detection:** Evaluates line lengths and terminal punctuation to stitch broken PDF lines back into continuous, reflowable paragraphs.
- **Structural HTML Tagging:** Automatically detects ALL-CAPS chapter titles and subtitles, applying `<h2>` and `<h3>` tags with custom CSS for proper visual hierarchy.
- **Dynamic Table of Contents:** Hunts for headings and dynamically generates a functional `toc.ncx` navigation map for e-reader menus.
- **Metadata Extraction:** Pulls the book title directly from the PDF's internal metadata (falling back to the filename if metadata is missing).
- **Safe File Handling:** Never overwrites existing files. Automatically appends `-1`, `-2`, etc., if an output filename already exists in the directory.

## Requirements

- Python 3
- `pypdf`

## Installation

Install the required text extraction library using pip:

```bash
python3 -m pip install pypdf
```

## Usage

Run the script from your terminal, passing the target PDF file as an argument: `python3 convert_pdf.py your_document.pdf`

Output The script will generate an .epub file in the same directory.

- If book.pdf is passed, it outputs book.epub.
- If book.epub already exists, it outputs book-1.epub.

## Customization

The internal CSS can be easily modified within the script to adjust the output typography.

**Default styling includes:**

- Justified text alignment
- 1.5em first-line paragraph indent
- Forced page breaks before h2 headings
