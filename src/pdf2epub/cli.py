"""Command-line interface for pdf2epub."""

import argparse
import sys
from typing import Optional, Sequence

from .api import convert_pdf
from .errors import Pdf2EpubError
from .models import ConversionOptions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2epub",
        description="Convert a text-based PDF into a reflowable EPUB 3.3 book.",
    )
    parser.add_argument("input", help="PDF file to convert")
    parser.add_argument("-o", "--output", help="destination EPUB file")
    parser.add_argument("--title", help="override the book title")
    parser.add_argument("--author", help="override the author")
    parser.add_argument("--language", help="BCP-47 language tag, for example en or zh-Hans")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace the exact output path instead of selecting a numbered suffix",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print resolved metadata and chapter information",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    options = ConversionOptions(
        title=arguments.title,
        author=arguments.author,
        language=arguments.language,
        overwrite=arguments.overwrite,
    )
    try:
        result = convert_pdf(arguments.input, arguments.output, options=options)
    except Pdf2EpubError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1

    for warning in result.warnings:
        print("Warning: {}".format(warning), file=sys.stderr)
    if arguments.verbose:
        print("Title: {}".format(result.title))
        print("Author: {}".format(result.author or "(not set)"))
        print("Language: {}".format(result.language))
        print("Identifier: {}".format(result.identifier))
        print("Chapters: {}".format(result.chapter_count))
    print("EPUB generated at: {}".format(result.output_path))
    return 0
