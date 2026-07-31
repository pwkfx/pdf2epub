"""PDF reading and layout-token extraction."""

from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from statistics import median
from typing import Any, List, Optional, Sequence, Tuple

from pypdf import PdfReader, mult
from pypdf.errors import PdfReadError as PyPdfReadError

from .errors import MissingDependencyError, NoExtractableTextError, PdfReadError
from .models import ExtractedDocument, ExtractedPage, TextLine


@dataclass(frozen=True)
class _Fragment:
    text: str
    x: float
    y: float
    font_size: float
    font_name: str


def extract_document(
    path: Path,
    *,
    ocr_enabled: bool = True,
    ocr_language: Optional[str] = None,
    publication_language: Optional[str] = None,
) -> ExtractedDocument:
    """Read a PDF once and return normalized pages plus source metadata."""

    try:
        reader = PdfReader(path, strict=False)
    except (OSError, PyPdfReadError, ValueError) as exc:
        raise PdfReadError("Unable to read PDF '{}': {}".format(path, exc)) from exc

    if reader.is_encrypted:
        raise PdfReadError("Password-protected PDFs are not supported.")

    title, author, language = _read_metadata(reader)
    outline_titles = _read_outline_titles(reader)
    pages: List[ExtractedPage] = []
    warnings: List[str] = []
    ocr_candidates: List[int] = []

    try:
        for number, page in enumerate(reader.pages, 1):
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            fragments: List[_Fragment] = []
            multiline_fragment = [False]

            def collect(
                text: Any,
                user_matrix: Any,
                text_matrix: Any,
                font_dictionary: Any,
                font_size: Any,
                fragments: List[_Fragment] = fragments,
                multiline_fragment: List[bool] = multiline_fragment,
            ) -> None:
                value = str(text or "")
                segments = [segment for segment in value.splitlines() if segment.strip()]
                if len(segments) > 1:
                    multiline_fragment[0] = True
                if not segments:
                    return
                try:
                    transform = mult(text_matrix, user_matrix)
                    x = float(transform[4])
                    y = float(transform[5])
                    size = float(font_size)
                except (IndexError, TypeError, ValueError):
                    return
                if not all(isfinite(item) for item in (x, y, size)) or size <= 0:
                    return
                font_name = ""
                if font_dictionary is not None:
                    font_name = str(font_dictionary.get("/BaseFont", ""))
                for segment in segments:
                    fragments.append(_Fragment(segment, x, y, size, font_name))

            plain_text = page.extract_text(visitor_text=collect) or ""
            lines = _layout_lines(
                fragments,
                plain_text,
                number,
                width,
                height,
                multiline_fragment[0],
            )
            if lines is None:
                lines = _fallback_lines(plain_text, number)
                if plain_text.strip():
                    warnings.append(
                        "Page {} did not expose reliable layout data; "
                        "text heuristics were used.".format(number)
                    )
            pages.append(ExtractedPage(number, width, height, tuple(lines)))
            if (
                not plain_text.strip()
                and not any(line.text.strip() for line in lines)
                and _page_has_renderable_content(page)
            ):
                ocr_candidates.append(number - 1)
    except MemoryError as exc:
        raise PdfReadError("PDF text extraction exhausted available memory.") from exc
    except (OSError, PyPdfReadError, ValueError) as exc:
        raise PdfReadError("Unable to extract PDF text: {}".format(exc)) from exc
    except Exception as exc:
        raise PdfReadError("Unexpected PDF extraction failure: {}".format(exc)) from exc

    ocr_page_count = 0
    if ocr_candidates and ocr_enabled:
        ocr_page_count = _ocr_pdf_pages(
            path,
            pages,
            ocr_candidates,
            requested_language=ocr_language,
            publication_language=publication_language or language,
            warnings=warnings,
        )

    if not any(line.text.strip() for page in pages for line in page.lines):
        if ocr_candidates and not ocr_enabled:
            detail = "OCR is disabled."
        elif ocr_candidates:
            detail = "OCR produced no usable text."
        else:
            detail = "The PDF appears to be empty."
        raise NoExtractableTextError(
            "No extractable text was found. {} Enable OCR for image-based PDFs.".format(detail)
        )

    return ExtractedDocument(
        pages=tuple(pages),
        title=title,
        author=author,
        language=language,
        outline_titles=outline_titles,
        warnings=tuple(warnings),
        ocr_page_count=ocr_page_count,
    )


def _page_has_renderable_content(page: Any) -> bool:
    try:
        contents = page.get_contents()
        if contents is None:
            return False
        return bool(contents.get_data().strip())
    except Exception:
        return True


def _ocr_pdf_pages(
    path: Path,
    pages: List[ExtractedPage],
    page_indexes: Sequence[int],
    *,
    requested_language: Optional[str],
    publication_language: Optional[str],
    warnings: List[str],
) -> int:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise MissingDependencyError(
            "PDF OCR rendering requires the 'pypdfium2' Python package."
        ) from exc

    from .ocr import recognize_image

    try:
        document = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise PdfReadError("Unable to render PDF pages for OCR: {}".format(exc)) from exc

    recognized_pages = 0
    try:
        for page_index in page_indexes:
            source_page = None
            bitmap = None
            try:
                source_page = document[page_index]
                bitmap = source_page.render(scale=300.0 / 72.0)
                image = bitmap.to_pil().copy()
                extracted_page = pages[page_index]
                result = recognize_image(
                    image,
                    extracted_page.number,
                    extracted_page.width,
                    extracted_page.height,
                    requested_language=requested_language,
                    publication_language=publication_language,
                    warnings=warnings,
                )
            except (MissingDependencyError, NoExtractableTextError):
                raise
            except Exception as exc:
                from .errors import OcrError

                if isinstance(exc, OcrError):
                    raise
                raise PdfReadError(
                    "Unable to render PDF page {} for OCR: {}".format(page_index + 1, exc)
                ) from exc
            finally:
                if bitmap is not None:
                    with suppress(Exception):
                        bitmap.close()
                if source_page is not None:
                    with suppress(Exception):
                        source_page.close()
            if result.lines:
                pages[page_index] = ExtractedPage(
                    extracted_page.number,
                    extracted_page.width,
                    extracted_page.height,
                    result.lines,
                )
                recognized_pages += 1
            else:
                warnings.append("OCR found no text on PDF page {}.".format(page_index + 1))
    finally:
        with suppress(Exception):
            document.close()
    return recognized_pages


def _read_metadata(reader: PdfReader) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    title: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    try:
        metadata = reader.metadata
        if metadata is not None:
            title = _clean_metadata_value(getattr(metadata, "title", None))
            author = _clean_metadata_value(getattr(metadata, "author", None))
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    with suppress(AttributeError, KeyError, TypeError, ValueError):
        language = _clean_metadata_value(reader.root_object.get("/Lang"))
    return title, author, language


def _clean_metadata_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def _read_outline_titles(reader: PdfReader) -> Tuple[str, ...]:
    titles: List[str] = []

    def visit(items: Sequence[Any]) -> None:
        for item in items:
            if isinstance(item, list):
                visit(item)
                continue
            title = getattr(item, "title", None)
            if title is None and isinstance(item, str):
                title = item
            cleaned = _clean_metadata_value(title)
            if cleaned:
                titles.append(cleaned)

    try:
        visit(reader.outline)
    except (AttributeError, KeyError, TypeError, ValueError):
        return ()
    return tuple(titles)


def _layout_lines(
    fragments: Sequence[_Fragment],
    plain_text: str,
    page_number: int,
    page_width: float,
    page_height: float,
    multiline_fragment: bool,
) -> Optional[List[TextLine]]:
    if not fragments or multiline_fragment:
        return None
    unique_positions = {(round(fragment.x, 1), round(fragment.y, 1)) for fragment in fragments}
    if len(unique_positions) < 1:
        return None

    font_sizes = [fragment.font_size for fragment in fragments]
    tolerance = max(2.0, median(font_sizes) * 0.35)
    groups: List[List[_Fragment]] = []
    for fragment in sorted(fragments, key=lambda item: (-item.y, item.x)):
        if not groups:
            groups.append([fragment])
            continue
        group_y = median(item.y for item in groups[-1])
        if abs(fragment.y - group_y) <= tolerance:
            groups[-1].append(fragment)
        else:
            groups.append([fragment])

    assembled: List[Tuple[str, float, float, float, str]] = []
    for group in groups:
        ordered = sorted(group, key=lambda item: item.x)
        text = _join_fragments(ordered).strip()
        if not text:
            continue
        total_weight = sum(max(1, len(item.text.strip())) for item in ordered)
        size = (
            sum(item.font_size * max(1, len(item.text.strip())) for item in ordered) / total_weight
        )
        font_name = max(ordered, key=lambda item: len(item.text.strip())).font_name
        assembled.append(
            (
                text,
                min(item.x for item in ordered),
                median(item.y for item in ordered),
                size,
                font_name,
            )
        )

    plain_characters = sum(not character.isspace() for character in plain_text)
    layout_characters = sum(not character.isspace() for item in assembled for character in item[0])
    if plain_characters and layout_characters < plain_characters * 0.7:
        return None

    lines: List[TextLine] = []
    for index, (text, x, y, size, font_name) in enumerate(assembled):
        previous_y = assembled[index - 1][2] if index else None
        next_y = assembled[index + 1][2] if index + 1 < len(assembled) else None
        gap_before = previous_y - y if previous_y is not None else None
        gap_after = y - next_y if next_y is not None else None
        normalized_font = font_name.casefold()
        estimated_width = len(text) * size * 0.5
        centered = abs((x + estimated_width / 2.0) - page_width / 2.0) <= page_width * 0.12
        lines.append(
            TextLine(
                text=text,
                page_number=page_number,
                x=x,
                y=y,
                font_size=size,
                font_name=font_name,
                bold="bold" in normalized_font,
                italic="italic" in normalized_font or "oblique" in normalized_font,
                centered=centered,
                gap_before=gap_before,
                blank_before=bool(gap_before is not None and gap_before > size * 1.6),
                blank_after=bool(gap_after is not None and gap_after > size * 1.6),
                page_start=index == 0,
                page_end=index + 1 == len(assembled),
            )
        )
    return lines


def _join_fragments(fragments: Sequence[_Fragment]) -> str:
    output = ""
    previous: Optional[_Fragment] = None
    for fragment in fragments:
        value = fragment.text
        if not output:
            output = value
        elif output[-1:].isspace() or value[:1].isspace():
            output += value
        else:
            assert previous is not None
            estimated_end = previous.x + len(previous.text.rstrip()) * previous.font_size * 0.5
            if fragment.x - estimated_end > max(1.0, fragment.font_size * 0.15):
                output += " "
            output += value
        previous = fragment
    return output


def _fallback_lines(plain_text: str, page_number: int) -> List[TextLine]:
    raw_lines = plain_text.splitlines()
    nonempty_indexes = [index for index, value in enumerate(raw_lines) if value.strip()]
    if not nonempty_indexes:
        return []
    first = nonempty_indexes[0]
    last = nonempty_indexes[-1]
    result: List[TextLine] = []
    blank_seen = True
    for index, raw_line in enumerate(raw_lines):
        text = raw_line.strip()
        if not text:
            blank_seen = True
            continue
        next_is_blank = index + 1 >= len(raw_lines) or not raw_lines[index + 1].strip()
        indent = len(raw_line) - len(raw_line.lstrip(" \t"))
        result.append(
            TextLine(
                text=text,
                page_number=page_number,
                x=float(indent),
                blank_before=blank_seen,
                blank_after=next_is_blank,
                page_start=index == first,
                page_end=index == last,
            )
        )
        blank_seen = False
    return result
