"""Command-line interface for pdf2epub."""

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from .api import convert_document, repair_epub
from .errors import InputFileError, Pdf2EpubError
from .models import ConversionOptions, RepairOptions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2epub",
        description=(
            "Convert PDF, Word, or DjVu documents into EPUB 3.3, or repair an existing EPUB."
        ),
    )
    parser.add_argument("input", help="document to convert or EPUB file to repair")
    parser.add_argument("-o", "--output", help="destination EPUB file")
    parser.add_argument("--title", help="override the converted document title")
    parser.add_argument("--author", help="override the converted document author")
    parser.add_argument(
        "--language",
        help="BCP-47 language tag for conversion, for example en or zh-Hans",
    )
    parser.add_argument(
        "--ocr-language",
        help="Tesseract OCR language code or combination, for example eng+rus",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="do not OCR image-only PDF or DjVu pages",
    )
    parser.add_argument(
        "--djvu-facsimile",
        action="store_true",
        help="keep DjVu pages as fixed images with selectable text instead of reflowing",
    )
    parser.add_argument(
        "--epubcheck-jar",
        help="EPUBCheck jar used to validate EPUB repairs",
    )
    parser.add_argument(
        "--full-repair",
        action="store_true",
        help="reconstruct EPUB metadata, chapters, text flow, and navigation as EPUB 3.3",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace the exact output path instead of selecting a numbered suffix",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print detailed conversion or repair information",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        input_suffix = Path(arguments.input).suffix.casefold()
        if input_suffix in {".pdf", ".doc", ".docx", ".djvu", ".djv"}:
            if arguments.full_repair:
                raise InputFileError("--full-repair applies only to EPUB repair.")
            if input_suffix in {".doc", ".docx"} and (
                arguments.ocr_language or arguments.no_ocr or arguments.djvu_facsimile
            ):
                raise InputFileError(
                    "OCR options and --djvu-facsimile do not apply to Word conversion."
                )
            if input_suffix == ".pdf" and arguments.djvu_facsimile:
                raise InputFileError("--djvu-facsimile applies only to DjVu conversion.")
            result = convert_document(
                arguments.input,
                arguments.output,
                options=ConversionOptions(
                    title=arguments.title,
                    author=arguments.author,
                    language=arguments.language,
                    overwrite=arguments.overwrite,
                    ocr_enabled=not arguments.no_ocr,
                    ocr_language=arguments.ocr_language,
                    djvu_facsimile=arguments.djvu_facsimile,
                ),
            )
            operation = "conversion"
        elif input_suffix == ".epub":
            if arguments.title or arguments.author or arguments.language:
                raise InputFileError(
                    "--title, --author, and --language apply only to PDF/Word/DjVu conversion."
                )
            if arguments.ocr_language or arguments.no_ocr or arguments.djvu_facsimile:
                raise InputFileError(
                    "OCR options and --djvu-facsimile apply only to document conversion."
                )
            result = repair_epub(
                arguments.input,
                arguments.output,
                options=RepairOptions(
                    epubcheck_jar=arguments.epubcheck_jar,
                    overwrite=arguments.overwrite,
                    full_repair=arguments.full_repair,
                ),
            )
            operation = "repair"
        else:
            raise InputFileError(
                "Input file must use .pdf or .epub, or one of .doc, .docx, .djvu, or .djv."
            )
    except Pdf2EpubError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1

    for warning in result.warnings:
        print("Warning: {}".format(warning), file=sys.stderr)
    if arguments.verbose and operation == "conversion":
        print("Source format: {}".format(result.source_format))
        print("Title: {}".format(result.title))
        print("Author: {}".format(result.author or "(not set)"))
        print("Language: {}".format(result.language))
        print("Identifier: {}".format(result.identifier))
        print("Chapters: {}".format(result.chapter_count))
        if result.page_count:
            print("Pages: {}".format(result.page_count))
        if result.image_count:
            print("Images: {}".format(result.image_count))
        if result.ocr_page_count:
            print("OCR pages: {}".format(result.ocr_page_count))
    elif arguments.verbose:
        print("EPUB version: {}".format(result.epub_version))
        if result.title:
            print("Title: {}".format(result.title))
        if result.author:
            print("Author: {}".format(result.author))
        if result.language:
            print("Language: {}".format(result.language))
        if result.chapter_count is not None:
            print("Chapters: {}".format(result.chapter_count))
        print(
            "EPUBCheck before: {} fatal, {} errors, {} warnings".format(
                result.before_fatal_count,
                result.before_error_count,
                result.before_warning_count,
            )
        )
        print(
            "EPUBCheck after: {} fatal, {} errors, {} warnings".format(
                result.after_fatal_count,
                result.after_error_count,
                result.after_warning_count,
            )
        )
        print("Repairs:")
        for fix in result.fixes:
            print("- {}".format(fix))
    message = "EPUB generated at" if operation == "conversion" else "EPUB repaired at"
    print("{}: {}".format(message, result.output_path))
    return 0
