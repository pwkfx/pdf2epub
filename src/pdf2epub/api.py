"""Public orchestration API."""

import os
from pathlib import Path
from typing import Optional, Union
from uuid import uuid4

from .epub import write_epub, write_publication
from .errors import InputFileError
from .models import (
    ConversionOptions,
    ConversionResult,
    PublicationMetadata,
    RepairOptions,
    RepairResult,
)
from .pdf import extract_document
from .repair import repair_epub_archive
from .structure import build_sections, detect_blocks

PathLike = Union[str, Path]


def convert_pdf(
    input_path: PathLike,
    output_path: Optional[PathLike] = None,
    *,
    options: Optional[ConversionOptions] = None,
) -> ConversionResult:
    """Convert a PDF into an EPUB publication, using OCR when needed.

    The default output is placed beside the input. Unless ``overwrite`` is enabled,
    an available ``-1``, ``-2``, ... suffix is selected when the destination exists.
    """

    source = _validate_input_path(Path(input_path))
    if source.suffix.casefold() != ".pdf":
        raise InputFileError("PDF conversion requires a file using the .pdf extension.")
    return _convert_document_source(source, output_path, options or ConversionOptions())


def convert_document(
    input_path: PathLike,
    output_path: Optional[PathLike] = None,
    *,
    options: Optional[ConversionOptions] = None,
) -> ConversionResult:
    """Convert PDF, DOC, DOCX, or DjVu input into an EPUB publication."""

    source = _validate_input_path(Path(input_path))
    if source.suffix.casefold() not in {".pdf", ".doc", ".docx", ".djvu", ".djv"}:
        raise InputFileError("Convertible input must use .pdf, .doc, .docx, .djvu, or .djv.")
    return _convert_document_source(source, output_path, options or ConversionOptions())


def _convert_document_source(
    source: Path,
    output_path: Optional[PathLike],
    conversion_options: ConversionOptions,
) -> ConversionResult:
    destination = _resolve_output_path(source, output_path, conversion_options.overwrite)
    suffix = source.suffix.casefold()
    if suffix == ".pdf":
        return _convert_pdf_source(source, destination, conversion_options)
    if suffix in {".doc", ".docx"}:
        return _convert_word_source(source, destination, conversion_options)
    return _convert_djvu_source(source, destination, conversion_options)


def _convert_pdf_source(
    source: Path,
    destination: Path,
    conversion_options: ConversionOptions,
) -> ConversionResult:
    document = extract_document(
        source,
        ocr_enabled=conversion_options.ocr_enabled,
        ocr_language=conversion_options.ocr_language,
        publication_language=conversion_options.language,
    )
    warnings = list(document.warnings)
    title = _metadata_value(conversion_options.title, document.title, source.stem)
    author = _metadata_value(conversion_options.author, document.author, None)
    language = _resolve_language(conversion_options.language, document.language, warnings)
    identifier = "urn:uuid:{}".format(uuid4())
    metadata = PublicationMetadata(title, author, language, identifier)

    blocks = detect_blocks(document)
    sections = build_sections(blocks, title)
    write_epub(destination, metadata, sections, overwrite=conversion_options.overwrite)

    chapter_count = max(1, sum(section.is_preface is False for section in sections))
    return ConversionResult(
        output_path=destination,
        title=title,
        author=author,
        language=language,
        identifier=identifier,
        chapter_count=chapter_count,
        warnings=tuple(warnings),
        source_format="pdf",
        page_count=len(document.pages),
        image_count=0,
        ocr_page_count=document.ocr_page_count,
    )


def _convert_word_source(
    source: Path,
    destination: Path,
    conversion_options: ConversionOptions,
) -> ConversionResult:
    from .word import build_word_publication, extract_word_document

    document = extract_word_document(source)
    warnings = list(document.warnings)
    title = _metadata_value(conversion_options.title, document.title, source.stem)
    author = _metadata_value(conversion_options.author, document.author, None)
    language = _resolve_language(conversion_options.language, document.language, warnings)
    identifier = "urn:uuid:{}".format(uuid4())
    metadata = PublicationMetadata(title, author, language, identifier)
    publication = build_word_publication(document, metadata)
    write_publication(
        destination,
        metadata,
        publication,
        overwrite=conversion_options.overwrite,
    )
    chapter_count = max(
        1,
        sum("chapter-" in section.filename for section in publication.sections),
    )
    return ConversionResult(
        output_path=destination,
        title=title,
        author=author,
        language=language,
        identifier=identifier,
        chapter_count=chapter_count,
        warnings=tuple(dict.fromkeys(warnings + list(publication.warnings))),
        source_format=source.suffix.casefold().lstrip("."),
        page_count=0,
        image_count=len(publication.resources),
        ocr_page_count=0,
    )


def _convert_djvu_source(
    source: Path,
    destination: Path,
    conversion_options: ConversionOptions,
) -> ConversionResult:
    from .djvu import build_djvu_publication

    warnings = []
    title = _metadata_value(conversion_options.title, None, source.stem)
    author = _metadata_value(conversion_options.author, None, None)
    language = _resolve_language(conversion_options.language, None, warnings)
    identifier = "urn:uuid:{}".format(uuid4())
    metadata = PublicationMetadata(title, author, language, identifier)
    publication = build_djvu_publication(
        source,
        metadata,
        ocr_enabled=conversion_options.ocr_enabled,
        ocr_language=conversion_options.ocr_language,
        facsimile=conversion_options.djvu_facsimile,
    )
    write_publication(
        destination,
        metadata,
        publication,
        overwrite=conversion_options.overwrite,
    )
    warnings.extend(publication.warnings)
    return ConversionResult(
        output_path=destination,
        title=title,
        author=author,
        language=language,
        identifier=identifier,
        chapter_count=max(
            1,
            sum("chapter-" in section.filename for section in publication.sections),
        ),
        warnings=tuple(dict.fromkeys(warnings)),
        source_format=source.suffix.casefold().lstrip("."),
        page_count=publication.page_count or len(publication.sections),
        image_count=len(publication.resources),
        ocr_page_count=publication.ocr_page_count,
    )


def repair_epub(
    input_path: PathLike,
    output_path: Optional[PathLike] = None,
    *,
    options: Optional[RepairOptions] = None,
) -> RepairResult:
    """Create an EPUBCheck-validated repaired copy of an EPUB.

    ``RepairOptions(full_repair=True)`` additionally reconstructs book metadata,
    chapter boundaries, reading order, and navigation as EPUB 3.3.
    """

    repair_options = options or RepairOptions()
    source = _validate_input_path(Path(input_path))
    destination = _resolve_repair_output_path(
        source,
        output_path,
        repair_options.overwrite,
        repair_options.full_repair,
    )
    outcome = repair_epub_archive(source, destination, repair_options)
    return RepairResult(
        output_path=destination,
        epub_version=outcome.epub_version,
        fixes=outcome.fixes,
        before_fatal_count=outcome.before.fatal_count,
        before_error_count=outcome.before.error_count,
        before_warning_count=outcome.before.warning_count,
        after_fatal_count=outcome.after.fatal_count,
        after_error_count=outcome.after.error_count,
        after_warning_count=outcome.after.warning_count,
        warnings=outcome.warnings,
        title=outcome.title,
        author=outcome.author,
        language=outcome.language,
        chapter_count=outcome.chapter_count,
    )


def _validate_input_path(path: Path) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InputFileError("Input file does not exist: {}".format(path)) from exc
    if not resolved.is_file():
        raise InputFileError("Input path is not a file: {}".format(path))
    return resolved


def _resolve_output_path(
    input_path: Path,
    output_path: Optional[PathLike],
    overwrite: bool,
) -> Path:
    if output_path is None:
        desired = input_path.with_suffix(".epub")
    else:
        desired = Path(output_path).expanduser()
        if not desired.suffix:
            desired = desired.with_suffix(".epub")
        try:
            desired = desired.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise InputFileError("Invalid output path: {}".format(output_path)) from exc
    if desired.suffix.casefold() != ".epub":
        raise InputFileError("Output file must use the .epub extension.")

    parent = desired.parent
    if not parent.exists() or not parent.is_dir():
        raise InputFileError("Output directory does not exist: {}".format(parent))
    if overwrite or not desired.exists():
        return desired

    counter = 1
    while True:
        candidate = desired.with_name("{}-{}{}".format(desired.stem, counter, desired.suffix))
        if not candidate.exists():
            return candidate
        counter += 1


def _resolve_repair_output_path(
    input_path: Path,
    output_path: Optional[PathLike],
    overwrite: bool,
    full_repair: bool = False,
) -> Path:
    if output_path is None:
        suffix = "rebuilt" if full_repair else "fixed"
        desired = input_path.with_name("{}-{}.epub".format(input_path.stem, suffix))
    else:
        desired = Path(output_path).expanduser()
        if not desired.suffix:
            desired = desired.with_suffix(".epub")
        try:
            desired = desired.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise InputFileError("Invalid output path: {}".format(output_path)) from exc
    if desired.suffix.casefold() != ".epub":
        raise InputFileError("Output file must use the .epub extension.")
    try:
        same_file = desired == input_path or (
            desired.exists() and os.path.samefile(str(desired), str(input_path))
        )
    except OSError:
        same_file = desired == input_path
    if same_file:
        raise InputFileError("Repaired EPUB output must not replace the source file.")

    parent = desired.parent
    if not parent.exists() or not parent.is_dir():
        raise InputFileError("Output directory does not exist: {}".format(parent))
    if overwrite or not desired.exists():
        return desired

    counter = 1
    while True:
        candidate = desired.with_name("{}-{}{}".format(desired.stem, counter, desired.suffix))
        if not candidate.exists():
            return candidate
        counter += 1


def _metadata_value(
    override: Optional[str],
    extracted: Optional[str],
    fallback: Optional[str],
) -> Optional[str]:
    for value in (override, extracted, fallback):
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _resolve_language(
    override: Optional[str],
    extracted: Optional[str],
    warnings: list,
) -> str:
    if override is not None:
        return _validate_language(override)
    if extracted is not None:
        try:
            return _validate_language(extracted)
        except InputFileError:
            warnings.append(
                "The source language metadata {!r} is invalid; using 'und'.".format(extracted)
            )
    return "und"


def _validate_language(value: str) -> str:
    import re

    language = str(value).strip()
    pattern = r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$"
    if not re.fullmatch(pattern, language):
        raise InputFileError("Language must be a BCP-47 tag such as 'en', 'ru', or 'zh-Hans'.")
    return language
