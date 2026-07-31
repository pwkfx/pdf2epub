# Multi-format EPUB Conversion and Repair

A Python 3.9+ command-line tool and library that converts PDF, Word, and DjVu documents into
EPUB 3.3 books and repairs existing EPUB 2 or EPUB 3 publications. PDF and DjVu conversion
uses layout-aware parsing to reconstruct paragraphs and navigation. Image-only PDF and DjVu
pages are OCRed automatically when required.

## Features

- Produces EPUB 3.3 with an XHTML navigation document and NCX fallback.
- Offers conservative structural repair and opt-in semantic EPUB 3.3 reconstruction.
- Conservative repair preserves the source EPUB version, metadata, content, and resources.
- Full repair recovers metadata, chapter files, headings, text flow, image descriptions,
  reading order, and matching navigation.
- Requires a clean EPUBCheck fatal/error result before publishing a repaired copy.
- Splits detected chapters into separate XHTML resources.
- Uses PDF font, position, spacing, outline, and text evidence when available.
- Extracts title and author metadata, with explicit command-line overrides.
- Writes beside the input by default and never overwrites unless requested.
- Validates manifest, spine, navigation resources, and anchors before writing.
- Writes atomically so failed conversions do not leave partial output files.
- Converts DOCX semantics—including headings, lists, tables, links, footnotes, emphasis, and
  embedded images—into sanitized reflowable XHTML.
- Converts legacy DOC through an isolated headless LibreOffice profile and the DOCX pipeline.
- Extracts native PDF text first and runs 300 DPI Tesseract OCR only on text-empty pages.
- Converts DjVu hidden text into reflowable XHTML while retaining cover scans and meaningful
  visual layers as image resources.
- Keeps early scanned legal/title/cover pages as page-separated images, suppresses their
  unreliable OCR overflow, and selects the strongest early visual as the package cover.
- Offers opt-in fixed-layout DjVu facsimiles with page images and transparent selectable text.
- Adds image resources, nested Word navigation, and DjVu page navigation to the EPUB package
  as applicable.

## Installation

Install the package and its `pdf2epub` command:

```bash
python3 -m pip install .
```

For development tools:

```bash
python3 -m pip install -e ".[dev]"
```

Python dependencies such as Mammoth, html5lib, pypdfium2, Pillow, pypdf, and pymorphy3 are
installed with the package. External programs are discovered only when their feature is used:

- [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) is required when a
  PDF or DjVu page needs OCR. Install the required language data too.
- [DjVuLibre](https://djvu.sourceforge.net/) (`djvused`, `djvutxt`, and `ddjvu`) is required
  for `.djvu` and `.djv` input.
- [LibreOffice](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)
  is required only for legacy `.doc` input. `.docx` is read directly.

For example:

```bash
# macOS with Homebrew
brew install tesseract tesseract-lang djvulibre libreoffice

# Debian or Ubuntu
sudo apt-get install tesseract-ocr tesseract-ocr-rus djvulibre-bin libreoffice-writer
```

`TESSERACT_CMD`, `DJVULIBRE_BIN`, and `SOFFICE_CMD` can point to nonstandard installations.
Pymorphy provides the Russian dictionary used for high-confidence full-repair OCR cleanup.

EPUB repair additionally requires Java and
[EPUBCheck](https://www.w3.org/publishing/epubcheck/). EPUBCheck is not bundled or downloaded
at runtime. Configure its jar using `--epubcheck-jar` or `EPUBCHECK_JAR`, or install an
`epubcheck` executable on `PATH`.

## Usage

```bash
pdf2epub INPUT [-o OUTPUT] [--title TITLE] [--author AUTHOR]
               [--language TAG] [--ocr-language CODE[+CODE]] [--no-ocr]
               [--djvu-facsimile] [--epubcheck-jar PATH] [--full-repair]
               [--overwrite] [-v]
```

Direct script execution remains supported from a source checkout:

```bash
python3 convert_pdf.py your_document.pdf
python3 convert_pdf.py damaged_book.epub --epubcheck-jar /path/to/epubcheck.jar
python3 convert_pdf.py damaged_book.epub --full-repair
```

Examples:

```bash
pdf2epub book.pdf
pdf2epub scanned-book.pdf --ocr-language eng+rus
pdf2epub report.docx
pdf2epub old-report.doc
pdf2epub archive.djvu --language ru
pdf2epub archive.djvu --djvu-facsimile
pdf2epub book.pdf --title "A Better Title" --author "Ada Example" --language en
pdf2epub book.pdf -o exports/book.epub --overwrite --verbose
pdf2epub damaged.epub --epubcheck-jar /path/to/epubcheck.jar --verbose
pdf2epub poorly-structured.epub --full-repair --verbose
```

Input type is selected from the case-insensitive `.pdf`, `.doc`, `.docx`, `.djvu`, `.djv`,
or `.epub` extension. `--title`, `--author`, and `--language` apply to document conversion.
`--ocr-language` accepts installed Tesseract codes such as `eng`, `rus`, or `eng+rus`;
`--no-ocr` disables automatic PDF and DjVu OCR. `--djvu-facsimile` selects fixed-layout DjVu
output; the default is a normal reflowable book.

Metadata precedence is command-line override, then source metadata when available, then a
fallback:

- Title falls back to the input filename.
- Author is omitted when unavailable.
- Language falls back to the BCP-47 `und` (undetermined) tag.

### EPUBCheck configuration

EPUB repair locates EPUBCheck in this order:

1. `--epubcheck-jar PATH`
2. `EPUBCHECK_JAR`
3. An `epubcheck` executable on `PATH`

When a jar is configured, Java is located through `JAVA_HOME`, common Homebrew OpenJDK
locations, or `PATH`. Validation warnings are printed but do not prevent output. Fatal errors
and errors prevent output and leave the source untouched.

## Output behavior

Document conversions and EPUB repairs are written atomically beside the input by default.

- If `book.pdf`, `book.docx`, or `book.djvu` is passed, it outputs `book.epub`.
- If `book.epub` already exists, it outputs `book-1.epub`.
- Additional collisions use `book-2.epub`, `book-3.epub`, and so on.
- If `damaged.epub` is passed, it outputs `damaged-fixed.epub`.
- Repair collisions use `damaged-fixed-1.epub`, `damaged-fixed-2.epub`, and so on.
- With `--full-repair`, `damaged.epub` outputs `damaged-rebuilt.epub`.
- Full-repair collisions use `damaged-rebuilt-1.epub`, `damaged-rebuilt-2.epub`, and so on.
- `--overwrite` replaces the exact requested path atomically.
- A suffix-less `--output` path automatically receives `.epub`.
- EPUB repair always preserves the source. An output path resolving to the source is rejected,
  including with `--overwrite`.

### Minimal EPUB repairs

The repair pipeline currently applies only known-safe changes:

- Remove an empty EPUB 2 guide.
- Add `alt=""` to images with no `alt` attribute.
- Replace invalid block-containing or body-level `span` wrappers with `div`.
- Move rows out of malformed `table → tr → td → tr` wrappers.
- Wrap orphan table-row runs in a table.
- Store `mimetype` first and uncompressed.

EPUBCheck runs before and against the temporary repaired archive. If other fatal errors or
errors remain, the command reports them and creates no final output.

### Full EPUB repair

`--full-repair` first applies the conservative fixes and then rebuilds the publication as
EPUB 3.3 with NCX fallback. It is intended for books that are technically damaged and also
poorly structured:

- Resolve title, author, and language from reliable visible book content when package
  metadata is generic or incorrect.
- Recover front matter, numbered chapters, appendices, and back matter from source headings.
- Create one XHTML resource per recovered section and regenerate the OPF manifest and spine.
- Generate a visible content page, `nav.xhtml`, and a matching NCX in recovered reading order.
- Promote demonstrated source heading styles to semantic `h1` and `h2` elements.
- Separate italic run-in topic labels as restrained `h3` headings without adding them to
  publication navigation; rejoin page-split topic bodies.
- Convert demonstrated OCR list markers (`•`, dot variants, `®`, `©`, squares, chevrons,
  asterisks, and isolated `o/о`) into semantic `ul`/`li` lists with one CSS-controlled bullet.
- Join embedded line-break hyphens conservatively, using Russian morphology for Cyrillic text.
- Correct only high-confidence, demonstrated Cyrillic OCR substitutions.
- Replace empty alternatives on recognized figures with contextual descriptions.
- Preserve CSS, fonts, images, and other non-content resources.

The original EPUB and conservative `-fixed` copy are never changed. Full repair is
deliberately opt-in because it changes document boundaries and may upgrade EPUB 2 sources.
The rebuilt publication is published atomically only after a clean EPUBCheck fatal/error
result. Warnings are allowed and reported.

## Python API

```python
from pdf2epub import ConversionOptions, convert_document

result = convert_document(
    "scanned-book.pdf",
    options=ConversionOptions(language="en", ocr_language="eng"),
)
print(result.output_path)
```

`convert_document` dispatches PDF, DOC, DOCX, DjVu, and DJV files. `convert_pdf` remains
available for callers that require PDF-only validation. `ConversionResult` includes resolved
metadata, publication UUID, source format, chapter and page counts, embedded image count, OCR
page count, and non-fatal warnings. Expected failures derive from `Pdf2EpubError`; focused
document, DjVu, OCR, and missing-dependency errors are public.

Repair an existing EPUB:

```python
from pdf2epub import RepairOptions, repair_epub

result = repair_epub(
    "damaged.epub",
    options=RepairOptions(epubcheck_jar="/path/to/epubcheck.jar"),
)
print(result.output_path)
print(result.fixes)
```

`RepairResult` includes EPUB version, applied fixes, before/after EPUBCheck counts, and
warnings. Full-repair results additionally include resolved title, author, language, and
chapter count:

```python
result = repair_epub(
    "poorly-structured.epub",
    options=RepairOptions(full_repair=True),
)
print(result.title, result.chapter_count)
```

Expected EPUB failures use focused read, repair, validation, and write exceptions under
`Pdf2EpubError`.

## Limitations

PDF and DjVu are presentation formats without reliable paragraph or heading semantics, so
structural detection is necessarily heuristic.

- Password-protected PDFs are rejected.
- PDF images, tables, hyperlinks, footnotes, columns, and detailed source formatting are not
  preserved; scan images are used for OCR but are not embedded in reflowable PDF output.
- PDF OCR replaces only pages with no meaningful native text. A wholly textless PDF fails
  when OCR is disabled or produces no text.
- Word output is semantic and reflowable rather than a visual reproduction of Word pages.
- DjVu reflow preserves hidden/OCR text, covers, and meaningful background visual layers.
  Foreground line art inseparably mixed with the DjVu text mask may not be recoverable as an
  independent illustration. Use `--djvu-facsimile` when exact page fidelity is required.
- A DjVu page remains as an image with a warning when OCR finds no words.
- OCR quality depends on installing the correct Tesseract language data. Explicit unavailable
  `--ocr-language` values fail; inferred unavailable languages fall back to English with a
  warning.
- Unusual transformations or malformed font coordinates may trigger text-only fallback.
- Very large uncompressed PDF content streams can require substantial memory in `pypdf`.
- Conservative EPUB repair does not correct OCR mistakes, prose, headings, TOC wording,
  metadata, or accessibility descriptions. Missing image alternatives receive the minimally
  valid empty value.
- Full repair uses conservative heuristics and built-in recovery evidence. It is not a
  substitute for human copy-editing; unresolved source OCR, malformed equations, and
  ambiguous table cell order can remain.
- Contextual figure descriptions help identify a figure and its surrounding subject but do
  not fully transcribe complex charts.
- DRM-protected, encrypted, ambiguous multi-rendition, unsafe, or structurally malformed
  archives are rejected.
- Conservative repair does not upgrade EPUB versions or attempt speculative fixes for
  unknown EPUBCheck errors. Full repair intentionally rebuilds recognized sources as EPUB 3.3.

## Development

```bash
ruff check .
ruff format --check .
pytest
```

Continuous integration runs on Python 3.9, 3.12, and 3.14 and validates converted and
repaired publications with EPUBCheck 5.3.0. A Python 3.12 job also exercises external tools.
Run those integration tests locally with:

```bash
PDF2EPUB_RUN_EXTERNAL_TESTS=1 pytest -m external_tools
```
