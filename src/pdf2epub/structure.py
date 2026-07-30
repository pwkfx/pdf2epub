"""Conservative structural detection and chapter construction."""

import re
from collections import Counter
from statistics import median
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .models import Block, ExtractedDocument, ExtractedPage, Section, TextLine

_TERMINAL_PUNCTUATION = (".", "!", "?", ":", ";", '"', "»", "'", "”", "’")
_SUBTITLE_PUNCTUATION = (".", "!", "?", ",", ":", ";")
_PAGE_NUMBER = re.compile(r"^(?:\d{1,5}|[ivxlcdm]{1,10})$", re.IGNORECASE)
_CHAPTER_LABEL = re.compile(
    r"^(?:chapter|part|book|section|volume|глава|часть|книга)\b",
    re.IGNORECASE,
)


def detect_blocks(document: ExtractedDocument) -> Tuple[Block, ...]:
    """Convert extracted page lines into headings, subtitles, and paragraphs."""

    pages = _remove_headers_and_footers(document.pages)
    lines = [line for page in pages for line in page.lines if line.text.strip()]
    if not lines:
        return ()

    body_size = _body_font_size(lines)
    body_margin = _body_left_margin(lines)
    median_width = _median_line_length(lines)
    outline_titles = {_normalize(value) for value in document.outline_titles}
    blocks: List[Block] = []
    paragraph_parts: List[str] = []
    previous_line: Optional[TextLine] = None

    def flush_paragraph() -> None:
        if not paragraph_parts:
            return
        text = re.sub(r"\s+", " ", "".join(paragraph_parts)).strip()
        if text:
            blocks.append(Block("paragraph", text))
        paragraph_parts.clear()

    for index, line in enumerate(lines):
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        if _is_heading(line, next_line, body_size, outline_titles, index == 0):
            flush_paragraph()
            blocks.append(Block("heading", line.text.strip()))
            previous_line = line
            continue

        if _is_subtitle(line, blocks, body_size):
            flush_paragraph()
            blocks.append(Block("subtitle", line.text.strip()))
            previous_line = line
            continue

        if paragraph_parts and _starts_new_paragraph(
            previous_line,
            line,
            body_size,
            body_margin,
            median_width,
        ):
            flush_paragraph()

        _append_line(paragraph_parts, line.text.strip())
        previous_line = line

    flush_paragraph()
    return tuple(blocks)


def build_sections(blocks: Sequence[Block], book_title: str) -> Tuple[Section, ...]:
    """Split blocks at top-level headings and assign deterministic resource names."""

    if not any(block.kind == "heading" for block in blocks):
        return (
            Section(
                filename="text/content.xhtml",
                title=book_title,
                blocks=tuple(blocks),
                navigation_id="content",
            ),
        )

    sections: List[Section] = []
    pending: List[Block] = []
    chapter_number = 0
    for block in blocks:
        if block.kind != "heading":
            pending.append(block)
            continue
        if pending:
            if chapter_number == 0:
                sections.append(
                    Section(
                        filename="text/preface.xhtml",
                        title="Preface",
                        blocks=tuple(pending),
                        navigation_id="preface",
                        is_preface=True,
                    )
                )
            else:
                sections.append(_chapter_section(chapter_number, pending))
            pending = []
        chapter_number += 1
        pending.append(block)

    if pending:
        sections.append(_chapter_section(chapter_number, pending))
    return tuple(sections)


def _chapter_section(number: int, blocks: Sequence[Block]) -> Section:
    heading = next((block.text for block in blocks if block.kind == "heading"), None)
    return Section(
        filename="text/chapter-{:03d}.xhtml".format(number),
        title=heading or "Chapter {}".format(number),
        blocks=tuple(blocks),
        navigation_id="chapter-{:03d}".format(number),
    )


def _remove_headers_and_footers(
    pages: Sequence[ExtractedPage],
) -> Tuple[ExtractedPage, ...]:
    if len(pages) < 3:
        return tuple(
            ExtractedPage(
                page.number,
                page.width,
                page.height,
                _drop_page_numbers(page),
            )
            for page in pages
        )

    candidates: Counter = Counter()
    zones: Dict[Tuple[int, int], Set[str]] = {}
    for page in pages:
        page_zones = _margin_candidates(page)
        zones[(page.number, 0)] = {_normalize(line.text) for line in page_zones[0]}
        zones[(page.number, 1)] = {_normalize(line.text) for line in page_zones[1]}
        candidates.update(zones[(page.number, 0)])
        candidates.update(zones[(page.number, 1)])

    threshold = max(2, int(len(pages) * 0.6 + 0.999))
    repeated = {value for value, count in candidates.items() if value and count >= threshold}
    cleaned: List[ExtractedPage] = []
    for page in pages:
        top, bottom = _margin_candidates(page)
        margin_lines = {id(line) for line in top + bottom}
        kept = []
        for line in page.lines:
            normalized = _normalize(line.text)
            is_margin = id(line) in margin_lines
            if is_margin and (
                normalized in repeated
                or (_PAGE_NUMBER.fullmatch(line.text.strip()) and _is_numeric_margin(line, page))
            ):
                continue
            kept.append(line)
        cleaned.append(ExtractedPage(page.number, page.width, page.height, tuple(kept)))
    return tuple(cleaned)


def _drop_page_numbers(page: ExtractedPage) -> Tuple[TextLine, ...]:
    top, bottom = _margin_candidates(page)
    margin_lines = {id(line) for line in top + bottom}
    return tuple(
        line
        for line in page.lines
        if not (
            id(line) in margin_lines
            and _PAGE_NUMBER.fullmatch(line.text.strip())
            and _is_numeric_margin(line, page)
        )
    )


def _is_numeric_margin(line: TextLine, page: ExtractedPage) -> bool:
    if line.y is not None:
        return line.y >= page.height * 0.9 or line.y <= page.height * 0.1
    return line.page_start or line.page_end


def _margin_candidates(
    page: ExtractedPage,
) -> Tuple[List[TextLine], List[TextLine]]:
    layout_lines = [line for line in page.lines if line.text.strip()]
    if not layout_lines:
        return [], []
    if all(line.y is not None for line in layout_lines):
        top = [line for line in layout_lines if line.y is not None and line.y >= page.height * 0.9]
        bottom = [
            line for line in layout_lines if line.y is not None and line.y <= page.height * 0.1
        ]
        return top, bottom
    return layout_lines[:2], layout_lines[-2:]


def _body_font_size(lines: Sequence[TextLine]) -> Optional[float]:
    weighted = []
    for line in lines:
        if line.font_size is None or not line.text.strip():
            continue
        weighted.extend([line.font_size] * max(1, min(20, len(line.text) // 8)))
    return median(weighted) if weighted else None


def _body_left_margin(lines: Sequence[TextLine]) -> Optional[float]:
    values = sorted(line.x for line in lines if line.x is not None and len(line.text) >= 20)
    if not values:
        return None
    return values[int((len(values) - 1) * 0.2)]


def _median_line_length(lines: Sequence[TextLine]) -> float:
    values = [len(line.text) for line in lines if len(line.text) >= 20]
    return float(median(values)) if values else 80.0


def _is_heading(
    line: TextLine,
    next_line: Optional[TextLine],
    body_size: Optional[float],
    outline_titles: Set[str],
    document_start: bool,
) -> bool:
    text = line.text.strip()
    if len(text) > 120 or not any(character.isalpha() for character in text):
        return False
    if _normalize(text) in outline_titles:
        return True

    separated_before = line.blank_before or line.page_start
    separated_after = line.blank_after or line.page_end
    if line.gap_before is not None and body_size is not None:
        separated_before = separated_before or line.gap_before > body_size * 1.5

    if body_size is not None and line.font_size is not None:
        large = line.font_size >= body_size * 1.35
        emphasized = line.bold and line.font_size >= body_size * 1.15
        return (large or emphasized) and (separated_before or separated_after or line.centered)

    uppercase = text.isupper()
    words = re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
    chapter_label = bool(_CHAPTER_LABEL.match(text))
    conservative_shape = len(words) >= 2 or chapter_label
    location_signal = separated_before and (separated_after or document_start or line.page_start)
    if next_line is not None and next_line.page_number != line.page_number:
        location_signal = separated_before and separated_after
    return uppercase and conservative_shape and location_signal


def _is_subtitle(
    line: TextLine,
    blocks: Sequence[Block],
    body_size: Optional[float],
) -> bool:
    if not blocks or blocks[-1].kind != "heading":
        return False
    text = line.text.strip()
    if not text or len(text) > 100 or text.endswith(_SUBTITLE_PUNCTUATION):
        return False
    if text.isupper():
        return False
    if body_size is None or line.font_size is None:
        return True
    return line.italic or line.centered or line.font_size >= body_size


def _starts_new_paragraph(
    previous: Optional[TextLine],
    current: TextLine,
    body_size: Optional[float],
    body_margin: Optional[float],
    median_width: float,
) -> bool:
    if previous is None:
        return False
    if current.blank_before:
        return True
    if (
        current.gap_before is not None
        and body_size is not None
        and current.gap_before > body_size * 1.5
    ):
        return True
    if current.x is not None and body_margin is not None:
        indent_threshold = body_size * 0.75 if body_size is not None else 1.5
        if current.x - body_margin > indent_threshold:
            return True

    previous_is_short = len(previous.text) < median_width * 0.7
    previous_is_terminal = previous.text.rstrip().endswith(_TERMINAL_PUNCTUATION)
    if previous.page_number != current.page_number:
        return previous_is_short and previous_is_terminal
    return previous_is_short and previous_is_terminal


def _append_line(parts: List[str], text: str) -> None:
    if not parts:
        parts.append(text)
    elif parts[-1].endswith("-"):
        parts.append(text.lstrip())
    else:
        parts.extend((" ", text))


def _normalize(value: str) -> str:
    collapsed = re.sub(r"\d+", "#", value.casefold())
    return re.sub(r"\s+", " ", collapsed).strip()
