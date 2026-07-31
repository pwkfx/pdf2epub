"""DjVuLibre-backed reflowable and facsimile EPUB preparation."""

import ast
import io
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

from .errors import DjvuReadError, MissingDependencyError
from .models import (
    Block,
    EpubResource,
    ExtractedDocument,
    ExtractedPage,
    NavigationEntry,
    PageEntry,
    PreparedPublication,
    PublicationMetadata,
    RenderedSection,
    TextLine,
)
from .structure import build_sections, detect_blocks

_XHTML_NS = "http://www.w3.org/1999/xhtml"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_PAGE = re.compile(r"\(page\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)")
_WORD = re.compile(
    r"\(word\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+"
    r'("(?:(?:\\.)|[^"\\])*")\s*\)',
    re.DOTALL,
)
_LINE = re.compile(
    r"\(line\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+"
    r'("(?:(?:\\.)|[^"\\])*")\s*\)',
    re.DOTALL,
)
_STRUCTURAL_LABEL = re.compile(
    r"^(?:chapter|part|book|section|introduction|conclusion|appendix|contents|"
    r"глава|часть|книга|введение|заключение|приложение|оглавление)\b",
    re.IGNORECASE,
)
_DISCRETIONARY_HYPHEN = "\u00ad"
_RUSSIAN_OCR_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё0-9'’]+")
_RUSSIAN_OCR_CHARACTERS = {
    "A": ("а",),
    "a": ("а",),
    "B": ("в",),
    "b": ("в",),
    "C": ("с", "е"),
    "c": ("с", "г"),
    "E": ("е",),
    "e": ("е",),
    "F": ("г",),
    "f": ("г",),
    "H": ("н",),
    "h": ("н",),
    "I": ("и", "ч", "ы", "л"),
    "i": ("и",),
    "K": ("к",),
    "k": ("к",),
    "L": ("ч",),
    "l": ("н",),
    "M": ("м",),
    "m": ("м",),
    "O": ("о",),
    "o": ("о",),
    "P": ("р",),
    "p": ("р",),
    "Q": ("а",),
    "q": ("а",),
    "T": ("т",),
    "t": ("т",),
    "X": ("х",),
    "x": ("х",),
    "Y": ("у",),
    "y": ("у",),
    "N": ("п",),
    "n": ("п",),
    "S": ("в",),
    "s": ("в",),
    "U": ("ц", "и"),
    "u": ("ц", "и"),
    "W": ("в",),
    "w": ("в",),
    "r": ("г",),
    "z": ("г",),
    "0": ("о",),
    "1": ("т", "ж", "л"),
    "2": ("г",),
    "3": ("з",),
}


@dataclass(frozen=True)
class _DjvuTools:
    djvused: str
    djvutxt: str
    ddjvu: str


@dataclass(frozen=True)
class _WordBox:
    text: str
    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class _LineBox:
    text: str
    left: float
    bottom: float
    width: float
    height: float


def build_djvu_publication(
    path: Path,
    metadata: PublicationMetadata,
    *,
    ocr_enabled: bool,
    ocr_language: Optional[str],
    facsimile: bool = False,
) -> PreparedPublication:
    """Prepare a reflowable DjVu EPUB, or an opt-in fixed facsimile."""

    if facsimile:
        return _build_djvu_facsimile_publication(
            path,
            metadata,
            ocr_enabled=ocr_enabled,
            ocr_language=ocr_language,
        )
    return _build_djvu_reflowable_publication(
        path,
        metadata,
        ocr_enabled=ocr_enabled,
        ocr_language=ocr_language,
    )


def _build_djvu_facsimile_publication(
    path: Path,
    metadata: PublicationMetadata,
    *,
    ocr_enabled: bool,
    ocr_language: Optional[str],
) -> PreparedPublication:
    """Render DjVu pages and retain native or OCR text as positioned spans."""

    tools = _find_tools()
    page_count = _page_count(tools, path)
    if page_count < 1:
        raise DjvuReadError("The DjVu document contains no pages.")
    try:
        from PIL import Image
    except ImportError as exc:
        raise MissingDependencyError(
            "DjVu conversion requires the 'Pillow' Python package."
        ) from exc

    sections: List[RenderedSection] = []
    resources: List[EpubResource] = []
    page_entries: List[PageEntry] = []
    warnings: List[str] = []
    ocr_page_count = 0

    with tempfile.TemporaryDirectory(prefix="pdf2epub-djvu-") as temporary_name:
        temporary = Path(temporary_name)
        for page_number in range(1, page_count + 1):
            native_words, native_size = _native_words(tools, path, page_number)
            render_dpi = 300 if not native_words and ocr_enabled else 200
            image = _render_page(tools, path, page_number, render_dpi, temporary, Image)
            words: Sequence[_WordBox]
            source_width: float
            source_height: float
            if native_words:
                words = native_words
                source_width, source_height = native_size
            elif ocr_enabled:
                from .ocr import recognize_image

                result = recognize_image(
                    image,
                    page_number,
                    float(image.width),
                    float(image.height),
                    requested_language=ocr_language,
                    publication_language=metadata.language,
                    warnings=warnings,
                )
                words = tuple(
                    _WordBox(
                        word.text,
                        float(word.left),
                        float(word.top),
                        float(word.width),
                        float(word.height),
                    )
                    for word in result.words
                )
                source_width = float(image.width)
                source_height = float(image.height)
                if words:
                    ocr_page_count += 1
                else:
                    warnings.append("OCR found no text on DjVu page {}.".format(page_number))
            else:
                words = ()
                source_width = float(image.width)
                source_height = float(image.height)

            display_image = _display_image(image, render_dpi, Image)
            asset_content, extension, media_type = _encode_page_image(display_image)
            asset_filename = "images/djvu-page-{:04d}{}".format(page_number, extension)
            resources.append(EpubResource(asset_filename, media_type, asset_content))
            scaled_words = _scale_words(
                words,
                source_width,
                source_height,
                float(display_image.width),
                float(display_image.height),
            )
            section_filename = "pages/page-{:04d}.xhtml".format(page_number)
            page_id = "page-{}".format(page_number)
            content = _page_xhtml(
                metadata.language,
                page_number,
                asset_filename,
                display_image.width,
                display_image.height,
                scaled_words,
                page_id,
            )
            sections.append(
                RenderedSection(
                    section_filename,
                    "Page {}".format(page_number),
                    content,
                    viewport=(display_image.width, display_image.height),
                )
            )
            page_entries.append(
                PageEntry(
                    str(page_number),
                    "{}#{}".format(section_filename, page_id),
                )
            )

    navigation = (NavigationEntry(metadata.title, page_entries[0].href),)
    return PreparedPublication(
        sections=tuple(sections),
        resources=tuple(resources),
        navigation=navigation,
        page_list=tuple(page_entries),
        fixed_layout=True,
        warnings=tuple(dict.fromkeys(warnings)),
        ocr_page_count=ocr_page_count,
        page_count=page_count,
    )


def _build_djvu_reflowable_publication(
    path: Path,
    metadata: PublicationMetadata,
    *,
    ocr_enabled: bool,
    ocr_language: Optional[str],
) -> PreparedPublication:
    tools = _find_tools()
    page_count = _page_count(tools, path)
    if page_count < 1:
        raise DjvuReadError("The DjVu document contains no pages.")
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError as exc:
        raise MissingDependencyError(
            "DjVu conversion requires the 'Pillow' Python package."
        ) from exc

    pages: List[ExtractedPage] = []
    resources: List[EpubResource] = []
    visual_blocks = {}
    cover_candidates: List[Tuple[float, int, int]] = []
    warnings: List[str] = []
    ocr_page_count = 0
    native_ocr_correction_count = 0
    body_started = False
    import pymorphy3

    # Mixed Cyrillic/Latin corruption is self-identifying, so repair it even when
    # a DjVu file has no usable language metadata and the publication is "und".
    morphology = pymorphy3.MorphAnalyzer()
    with tempfile.TemporaryDirectory(prefix="pdf2epub-djvu-") as temporary_name:
        temporary = Path(temporary_name)
        for page_number in range(1, page_count + 1):
            native_lines, page_size = _native_lines(tools, path, page_number)
            native_lines, correction_count = _repair_russian_ocr_lines(
                native_lines,
                morphology,
            )
            native_ocr_correction_count += correction_count
            width, height = page_size
            has_structural_label = any(
                _STRUCTURAL_LABEL.match(line.text.strip()) for line in native_lines
            )
            eligible_front_matter = (
                page_number <= 5 and not body_started and not has_structural_label
            )
            front_matter_facsimile = False
            page_lines: Sequence[TextLine]
            visual = None
            visual_alt = ""
            if native_lines:
                page_lines = _line_boxes_to_text_lines(
                    native_lines,
                    page_number,
                    width,
                    height,
                )
                visual = _background_visual(
                    tools,
                    path,
                    page_number,
                    temporary,
                    Image,
                    ImageChops,
                    ImageStat,
                )
                visual_alt = "Visual content from source page {}".format(page_number)
                likely_cover = visual is not None and _cover_score(visual, ImageStat) >= 2.0
                if eligible_front_matter and (len(native_lines) <= 15 or likely_cover):
                    rendered = _render_page(
                        tools,
                        path,
                        page_number,
                        200,
                        temporary,
                        Image,
                    )
                    if not _blank_image(rendered, ImageStat):
                        visual = rendered
                        page_lines = ()
                        front_matter_facsimile = True
                        visual_alt = "Front matter from source page {}".format(page_number)
            else:
                render_dpi = 300 if ocr_enabled else 200
                rendered = _render_page(
                    tools,
                    path,
                    page_number,
                    render_dpi,
                    temporary,
                    Image,
                )
                if ocr_enabled:
                    from .ocr import recognize_image

                    result = recognize_image(
                        rendered,
                        page_number,
                        float(rendered.width),
                        float(rendered.height),
                        requested_language=ocr_language,
                        publication_language=metadata.language,
                        warnings=warnings,
                    )
                    page_lines = result.lines
                    if page_lines:
                        ocr_page_count += 1
                        visual_alt = _xml_text(" ".join(line.text for line in page_lines))[:500]
                    else:
                        warnings.append("OCR found no text on DjVu page {}.".format(page_number))
                else:
                    page_lines = ()
                visual = _display_image(rendered, render_dpi, Image)
                if _blank_image(visual, ImageStat):
                    visual = None
                elif eligible_front_matter and (
                    len(page_lines) <= 15 or _cover_score(visual, ImageStat) >= 2.0
                ):
                    page_lines = ()
                    front_matter_facsimile = True
                    visual_alt = "Front matter from source page {}".format(page_number)

            if (
                has_structural_label
                or (eligible_front_matter and not front_matter_facsimile and len(page_lines) > 15)
                or page_number >= 5
            ):
                body_started = True

            pages.append(
                ExtractedPage(
                    page_number,
                    width or float(visual.width if visual is not None else 1),
                    height or float(visual.height if visual is not None else 1),
                    tuple(page_lines),
                )
            )
            if visual is not None:
                asset_content, extension, media_type = _encode_page_image(visual)
                asset_filename = "images/djvu-visual-{:04d}{}".format(page_number, extension)
                resource_index = len(resources)
                resources.append(
                    EpubResource(
                        asset_filename,
                        media_type,
                        asset_content,
                    )
                )
                visual_blocks[page_number] = Block(
                    "front-image" if front_matter_facsimile else "image",
                    "",
                    page_number=page_number,
                    resource_href=asset_filename,
                    alt=visual_alt,
                )
                if front_matter_facsimile and page_number <= 5:
                    cover_candidates.append(
                        (
                            _cover_score(visual, ImageStat),
                            resource_index,
                            page_number,
                        )
                    )

    if cover_candidates:
        _, resource_index, cover_page = max(cover_candidates)
        resource = resources[resource_index]
        resources[resource_index] = EpubResource(
            resource.filename,
            resource.media_type,
            resource.content,
            properties="cover-image",
        )
        cover_block = visual_blocks[cover_page]
        visual_blocks[cover_page] = Block(
            cover_block.kind,
            cover_block.text,
            page_number=cover_block.page_number,
            resource_href=cover_block.resource_href,
            alt="Cover of {}".format(metadata.title),
        )

    document = ExtractedDocument(
        pages=tuple(pages),
        title=metadata.title,
        author=metadata.author,
        language=metadata.language,
        outline_titles=(),
        warnings=(),
        ocr_page_count=ocr_page_count,
    )
    blocks, joined_correction_count = _repair_russian_ocr_blocks(
        detect_blocks(document),
        morphology,
    )
    native_ocr_correction_count += joined_correction_count
    if native_ocr_correction_count:
        warnings.append(
            "Corrected {} likely OCR-corrupted Russian words in the DjVu text layer.".format(
                native_ocr_correction_count
            )
        )
    blocks = _demote_sparse_front_matter_headings(blocks, pages)
    blocks = _insert_visual_blocks(blocks, visual_blocks)
    source_sections = build_sections(blocks, metadata.title)
    from .epub import _section_xhtml

    sections = tuple(
        RenderedSection(
            section.filename,
            section.title,
            _section_xhtml(metadata, section),
        )
        for section in source_sections
    )
    navigation = tuple(
        NavigationEntry(
            section.title,
            "{}#{}".format(section.filename, section.navigation_id)
            if any(block.kind == "heading" for block in section.blocks)
            else section.filename,
        )
        for section in source_sections
    )
    return PreparedPublication(
        sections=sections,
        resources=tuple(resources),
        navigation=navigation,
        fixed_layout=False,
        warnings=tuple(dict.fromkeys(warnings)),
        ocr_page_count=ocr_page_count,
        page_count=page_count,
    )


def _find_tools() -> _DjvuTools:
    configured_directory = os.environ.get("DJVULIBRE_BIN")

    def find(name: str) -> Optional[str]:
        candidates = []
        if configured_directory:
            candidates.append(str(Path(configured_directory) / name))
        candidates.extend(
            [
                shutil.which(name),
                "/opt/homebrew/bin/{}".format(name),
                "/usr/local/bin/{}".format(name),
            ]
        )
        return next(
            (str(candidate) for candidate in candidates if candidate and Path(candidate).is_file()),
            None,
        )

    djvused = find("djvused")
    djvutxt = find("djvutxt")
    ddjvu = find("ddjvu")
    missing = [
        name
        for name, value in (("djvused", djvused), ("djvutxt", djvutxt), ("ddjvu", ddjvu))
        if value is None
    ]
    if missing:
        raise MissingDependencyError(
            "DjVu conversion requires DjVuLibre tools (missing: {}). Install "
            "'djvulibre-bin' or set DJVULIBRE_BIN.".format(", ".join(missing))
        )
    return _DjvuTools(str(djvused), str(djvutxt), str(ddjvu))


def _page_count(tools: _DjvuTools, path: Path) -> int:
    completed = _run(
        [tools.djvused, str(path), "-e", "n"],
        "determine the DjVu page count",
    )
    matches = re.findall(r"\d+", completed.stdout)
    if not matches:
        raise DjvuReadError("DjVuLibre returned an invalid page count.")
    return int(matches[-1])


def _native_words(
    tools: _DjvuTools,
    path: Path,
    page_number: int,
) -> Tuple[Tuple[_WordBox, ...], Tuple[float, float]]:
    try:
        completed = subprocess.run(
            [
                tools.djvutxt,
                "--page={}".format(page_number),
                "--detail=word",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise DjvuReadError(
            "DjVu text extraction timed out on page {}.".format(page_number)
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise DjvuReadError("Unable to run djvutxt: {}".format(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        normalized = detail.casefold()
        if "no text" in normalized or "without text" in normalized:
            return (), (0.0, 0.0)
        raise DjvuReadError(
            "Unable to extract text from DjVu page {}: {}".format(
                page_number, detail or "unknown error"
            )
        )

    page_match = _PAGE.search(completed.stdout)
    page_width = 0.0
    page_height = 0.0
    page_left = 0.0
    page_bottom = 0.0
    if page_match:
        x1, y1, x2, y2 = (float(value) for value in page_match.groups())
        page_left = min(x1, x2)
        page_bottom = min(y1, y2)
        page_width = abs(x2 - x1)
        page_height = abs(y2 - y1)
    words: List[_WordBox] = []
    for match in _WORD.finditer(completed.stdout):
        x1, y1, x2, y2 = (float(value) for value in match.groups()[:4])
        text = _decode_zone_text(match.group(5))
        if not text:
            continue
        if page_width <= 0:
            page_width = max(page_width, x1, x2)
        if page_height <= 0:
            page_height = max(page_height, y1, y2)
        words.append(
            _WordBox(
                text,
                min(x1, x2) - page_left,
                max(0.0, page_height - (max(y1, y2) - page_bottom)),
                max(1.0, abs(x2 - x1)),
                max(1.0, abs(y2 - y1)),
            )
        )
    return tuple(words), (page_width, page_height)


def _native_lines(
    tools: _DjvuTools,
    path: Path,
    page_number: int,
) -> Tuple[Tuple[_LineBox, ...], Tuple[float, float]]:
    try:
        completed = subprocess.run(
            [
                tools.djvutxt,
                "--page={}".format(page_number),
                "--detail=line",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise DjvuReadError(
            "DjVu text extraction timed out on page {}.".format(page_number)
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise DjvuReadError("Unable to run djvutxt: {}".format(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        normalized = detail.casefold()
        if "no text" in normalized or "without text" in normalized:
            return (), (0.0, 0.0)
        raise DjvuReadError(
            "Unable to extract text from DjVu page {}: {}".format(
                page_number, detail or "unknown error"
            )
        )

    page_match = _PAGE.search(completed.stdout)
    if page_match:
        page_x1, page_y1, page_x2, page_y2 = (float(value) for value in page_match.groups())
        page_left = min(page_x1, page_x2)
        page_bottom = min(page_y1, page_y2)
        page_width = abs(page_x2 - page_x1)
        page_height = abs(page_y2 - page_y1)
    else:
        page_left = 0.0
        page_bottom = 0.0
        page_width = 0.0
        page_height = 0.0
    lines: List[_LineBox] = []
    raw_zones = []
    for match in _LINE.finditer(completed.stdout):
        x1, y1, x2, y2 = (float(value) for value in match.groups()[:4])
        text = _decode_zone_text(match.group(5))
        if text:
            raw_zones.append((x1, y1, x2, y2, text))
            if page_match is None:
                page_width = max(page_width, x1, x2)
                page_height = max(page_height, y1, y2)
    for x1, y1, x2, y2, text in raw_zones:
        lines.append(
            _LineBox(
                text,
                min(x1, x2) - page_left,
                min(y1, y2) - page_bottom,
                max(1.0, abs(x2 - x1)),
                max(1.0, abs(y2 - y1)),
            )
        )
    return tuple(lines), (page_width, page_height)


def _decode_zone_text(value: str) -> str:
    try:
        text = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        text = value[1:-1]
    normalized = re.sub(
        r"\x1f\s*$",
        _DISCRETIONARY_HYPHEN,
        str(text),
    ).replace("\x1f", "-")
    return _xml_text(" ".join(normalized.split()))


def _repair_russian_ocr_lines(
    boxes: Sequence[_LineBox],
    morphology: object,
) -> Tuple[Tuple[_LineBox, ...], int]:
    repaired: List[_LineBox] = []
    correction_count = 0
    for box in boxes:
        text, count = _repair_russian_ocr_text(box.text, morphology)
        repaired.append(
            _LineBox(
                text,
                box.left,
                box.bottom,
                box.width,
                box.height,
            )
        )
        correction_count += count
    return tuple(repaired), correction_count


def _repair_russian_ocr_blocks(
    blocks: Sequence[Block],
    morphology: object,
) -> Tuple[Tuple[Block, ...], int]:
    repaired = []
    correction_count = 0
    for block in blocks:
        text, count = _repair_russian_ocr_text(block.text, morphology)
        repaired.append(
            Block(
                block.kind,
                text,
                page_number=block.page_number,
                resource_href=block.resource_href,
                alt=block.alt,
            )
        )
        correction_count += count
    return tuple(repaired), correction_count


def _repair_russian_ocr_text(
    text: str,
    morphology: object,
) -> Tuple[str, int]:
    """Repair dictionary-backed mixed-script corruption in Russian OCR text."""

    line_uppercase = _looks_like_uppercase_line(text)
    letters = [character for character in text if character.isalpha()]
    cyrillic_fraction = (
        sum(bool(re.fullmatch(r"[А-Яа-яЁё]", character)) for character in letters) / len(letters)
        if letters
        else 0.0
    )
    correction_count = 0
    text, initial_count = re.subn(
        r"\b[rR]\.(?=[A-ZА-ЯЁ]\.)",
        "Г.",
        text,
    )
    correction_count += initial_count

    def replace_broken_sushchnost(match: "re.Match[str]") -> str:
        nonlocal correction_count
        candidate = "{}у{}".format(match.group(1), match.group(2))
        if not morphology.word_is_known(candidate.casefold()):
            return match.group(0)
        correction_count += 1
        return candidate.upper() if line_uppercase else candidate.casefold()

    text = re.sub(
        r"\b([CСс])-([Щщ][А-Яа-яЁё]+)\b",
        replace_broken_sushchnost,
        text,
    )

    def replace_token(match: "re.Match[str]") -> str:
        nonlocal correction_count
        source = match.group(0)
        has_cyrillic = bool(re.search(r"[А-Яа-яЁё]", source))
        has_latin = bool(re.search(r"[A-Za-z]", source))
        has_digit = bool(re.search(r"\d", source))
        has_foreign = bool(re.search(r"[A-Za-z0-9]", source))
        suspicious_case = _has_suspicious_russian_case(source)
        pure_cyrillic_confusion = bool(
            has_cyrillic
            and not has_foreign
            and not morphology.word_is_known(source.casefold())
            and any(
                pattern in source.casefold()
                for pattern in (
                    "сушност",
                    "системообразуюш",
                    "рчевид",
                    "моносраф",
                )
            )
        )
        contextual_latin_ocr = bool(
            not has_cyrillic
            and has_latin
            and not has_digit
            and cyrillic_fraction >= 0.5
            and (
                "r" in source
                or (
                    any(character.isupper() for character in source[1:])
                    and any(character.islower() for character in source)
                )
            )
        )
        if (
            not (has_cyrillic and has_foreign)
            and not suspicious_case
            and not contextual_latin_ocr
            and not pure_cyrillic_confusion
        ):
            return source

        candidates = _russian_ocr_candidates(source)
        if pure_cyrillic_confusion:
            candidates.extend(_pure_cyrillic_ocr_candidates(source))
        if not candidates:
            return source
        known = [
            (candidate, cost)
            for candidate, cost in candidates
            if morphology.word_is_known(candidate)
        ]
        if known:
            candidate = min(known, key=lambda item: item[1])[0]
        elif len(candidates) == 1 and has_cyrillic and has_latin and not has_digit:
            candidate = candidates[0][0]
        elif suspicious_case and morphology.word_is_known(source.casefold()):
            candidate = source.casefold()
        else:
            return source

        replacement = _russian_ocr_case(
            source,
            candidate,
            text[: match.start()],
            line_uppercase,
        )
        if replacement == source:
            return source
        correction_count += 1
        return replacement

    return _RUSSIAN_OCR_TOKEN.sub(replace_token, text), correction_count


def _russian_ocr_candidates(source: str) -> List[Tuple[str, int]]:
    normalized = source
    for pattern, replacement in (
        (r"1l[Щщ]", "жд"),
        (r"l[Щщ]", "нд"),
        (r"[Шш]l", "н"),
        (r"[bЬ]I", "ы"),
        (r"[ыЫ]I", "ы"),
        (r"LI", "ч"),
        (r"J[Il]", "л"),
        (r"Ll", "ч"),
        (r"ll", "н"),
        (r"l1", "н"),
        (r"I1", "п"),
        (r"['’]I", "ч"),
        (r"11[OО]", "по"),
        (r"110", "по"),
    ):
        normalized = re.sub(pattern, replacement, normalized)

    candidates: List[Tuple[str, int]] = [("", 0)]
    for character in normalized:
        if re.fullmatch(r"[А-Яа-яЁё]", character):
            options = (character.casefold(),)
        else:
            options = _RUSSIAN_OCR_CHARACTERS.get(character)
        if options is None:
            return []
        expanded = []
        for prefix, cost in candidates:
            for option_index, option in enumerate(options):
                expanded.append((prefix + option, cost + option_index))
        candidates = expanded[:64]

    unique = {}
    for candidate, cost in candidates:
        unique[candidate] = min(cost, unique.get(candidate, cost))
    return sorted(unique.items(), key=lambda item: (item[1], item[0]))


def _pure_cyrillic_ocr_candidates(source: str) -> List[Tuple[str, int]]:
    folded = source.casefold()
    candidates = []
    for pattern, replacement in (
        ("сушност", "сущност"),
        ("системообразуюш", "системообразующ"),
        ("рчевид", "очевид"),
        ("моносраф", "монограф"),
    ):
        if pattern in folded:
            candidates.append((folded.replace(pattern, replacement, 1), 1))
    return candidates


def _looks_like_uppercase_line(text: str) -> bool:
    letters = [character for character in text if character.isalpha()]
    if len(letters) < 4 or len(text) > 140:
        return False
    uppercase = sum(character.isupper() for character in letters)
    return uppercase / len(letters) >= 0.65


def _has_suspicious_russian_case(source: str) -> bool:
    if re.search(r"[^А-Яа-яЁё]", source):
        return False
    uppercase = sum(character.isupper() for character in source)
    lowercase = sum(character.islower() for character in source)
    return lowercase >= 2 and (
        uppercase >= 2 or any(character.isupper() for character in source[1:])
    )


def _russian_ocr_case(
    source: str,
    candidate: str,
    prefix: str,
    line_uppercase: bool,
) -> str:
    if line_uppercase:
        return candidate.upper()
    if re.search(r"(?:^|[.!?…]\s*[«\"“„]?)\s*$", prefix):
        return candidate.capitalize() if re.match(r"[А-ЯЁ]", source[:1]) else candidate
    if re.search(r"\b[А-ЯЁA-Z]\.\s*$", prefix) and source[:1].isupper():
        return candidate.capitalize()
    return candidate


def _line_boxes_to_text_lines(
    boxes: Sequence[_LineBox],
    page_number: int,
    page_width: float,
    page_height: float,
) -> Tuple[TextLine, ...]:
    lines: List[TextLine] = []
    body_height = median(box.height for box in boxes) if boxes else 1.0
    for index, box in enumerate(boxes):
        previous = boxes[index - 1] if index else None
        following = boxes[index + 1] if index + 1 < len(boxes) else None
        box_top = box.bottom + box.height
        previous_bottom = previous.bottom if previous is not None else None
        following_top = following.bottom + following.height if following is not None else None
        gap_before = previous_bottom - box_top if previous_bottom is not None else None
        gap_after = box.bottom - following_top if following_top is not None else None
        centered = abs((box.left + box.width / 2.0) - page_width / 2.0) <= page_width * 0.12
        lines.append(
            TextLine(
                text=box.text,
                page_number=page_number,
                x=box.left,
                y=box.bottom,
                # Individual DjVu box heights are noisy, but a stable page median
                # gives the paragraph detector a useful coordinate scale.
                font_size=body_height,
                centered=centered,
                gap_before=gap_before,
                blank_before=bool(gap_before is not None and gap_before > body_height * 1.25),
                blank_after=bool(gap_after is not None and gap_after > body_height * 1.25),
                page_start=index == 0,
                page_end=index + 1 == len(boxes),
                font_size_reliable=False,
            )
        )
    return tuple(lines)


def _render_page(
    tools: _DjvuTools,
    path: Path,
    page_number: int,
    dpi: int,
    temporary: Path,
    image_module: object,
    mode: Optional[str] = None,
) -> object:
    mode_suffix = "-{}".format(mode) if mode else ""
    output = temporary / "page-{:04d}-{}{}.pnm".format(page_number, dpi, mode_suffix)
    command = [
        tools.ddjvu,
        "-format=pnm",
        "-page={}".format(page_number),
        "-scale={}".format(dpi),
    ]
    if mode:
        command.append("-mode={}".format(mode))
    command.extend([str(path), str(output)])
    _run(
        command,
        "render DjVu page {}".format(page_number),
        timeout=300,
    )
    try:
        with image_module.open(str(output)) as opened:
            opened.load()
            return opened.copy()
    except (OSError, ValueError) as exc:
        raise DjvuReadError(
            "DjVuLibre produced an invalid image for page {}: {}".format(page_number, exc)
        ) from exc
    finally:
        with suppress(OSError):
            output.unlink()


def _background_visual(
    tools: _DjvuTools,
    path: Path,
    page_number: int,
    temporary: Path,
    image_module: object,
    image_chops: object,
    image_stat: object,
) -> Optional[object]:
    try:
        image = _render_page(
            tools,
            path,
            page_number,
            100,
            temporary,
            image_module,
            mode="background",
        ).convert("RGB")
    except DjvuReadError:
        return None
    statistics = image_stat.Stat(image)
    if max(statistics.stddev) < 3.0:
        return None
    corners = [
        image.getpixel((0, 0)),
        image.getpixel((image.width - 1, 0)),
        image.getpixel((0, image.height - 1)),
        image.getpixel((image.width - 1, image.height - 1)),
    ]
    background = tuple(round(median(pixel[channel] for pixel in corners)) for channel in range(3))
    solid = image_module.new("RGB", image.size, background)
    difference = image_chops.difference(image, solid).convert("L")
    mask = difference.point(lambda value: 255 if value > 12 else 0)
    bounding_box = mask.getbbox()
    if bounding_box is None:
        return None
    left, top, right, bottom = bounding_box
    area = max(0, right - left) * max(0, bottom - top)
    if area < image.width * image.height * 0.005:
        return None
    return image.crop(bounding_box)


def _blank_image(image: object, image_stat: object) -> bool:
    statistics = image_stat.Stat(image.convert("L"))
    return statistics.mean[0] >= 250.0 and statistics.stddev[0] < 1.5


def _cover_score(image: object, image_stat: object) -> float:
    """Prefer substantial, colorful early-page artwork over legal/title text."""

    thumbnail = image.convert("RGB")
    thumbnail.thumbnail((256, 256))
    pixels = list(thumbnail.getdata())
    if not pixels:
        return 0.0
    nonwhite_fraction = sum(sum(pixel) / 3.0 < 242.0 for pixel in pixels) / len(pixels)
    color_fraction = sum(max(pixel) - min(pixel) for pixel in pixels) / (255.0 * len(pixels))
    contrast = sum(image_stat.Stat(thumbnail).stddev) / (3.0 * 128.0)
    return nonwhite_fraction * 4.0 + color_fraction * 3.0 + contrast


def _insert_visual_blocks(
    blocks: Sequence[Block],
    visual_blocks: Dict[int, Block],
) -> Tuple[Block, ...]:
    if not visual_blocks:
        return tuple(blocks)
    result: List[Block] = []
    pending_pages = sorted(visual_blocks)
    inserted = set()
    for block in blocks:
        page_number = block.page_number or 0
        for visual_page in pending_pages:
            if visual_page > page_number or visual_page in inserted:
                continue
            result.append(visual_blocks[visual_page])
            inserted.add(visual_page)
        result.append(block)
    for visual_page in pending_pages:
        if visual_page not in inserted:
            result.append(visual_blocks[visual_page])
    return tuple(result)


def _demote_sparse_front_matter_headings(
    blocks: Sequence[Block],
    pages: Sequence[ExtractedPage],
) -> Tuple[Block, ...]:
    """Keep title-page display lines out of the chapter navigation."""

    sparse_pages = {
        page.number
        for page in pages
        if page.number <= 5
        and len(page.lines) <= 15
        and not any(_STRUCTURAL_LABEL.match(line.text.strip()) for line in page.lines)
    }
    return tuple(
        Block(
            "paragraph",
            block.text,
            page_number=block.page_number,
            resource_href=block.resource_href,
            alt=block.alt,
        )
        if block.kind == "heading" and block.page_number in sparse_pages
        else block
        for block in blocks
    )


def _run(
    command: Sequence[str],
    operation: str,
    *,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DjvuReadError("DjVuLibre timed out while trying to {}.".format(operation)) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise DjvuReadError("Unable to {}: {}".format(operation, exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise DjvuReadError(
            "Unable to {}: {}".format(operation, detail or "unknown DjVuLibre error")
        )
    return completed


def _display_image(image: object, render_dpi: int, image_module: object) -> object:
    if render_dpi == 200:
        return image
    width = max(1, round(float(image.width) * 200.0 / render_dpi))
    height = max(1, round(float(image.height) * 200.0 / render_dpi))
    resampling = getattr(image_module, "Resampling", image_module).LANCZOS
    return image.resize((width, height), resampling)


def _encode_page_image(image: object) -> Tuple[bytes, str, str]:
    output = io.BytesIO()
    try:
        colors = image.convert("RGB").getcolors(maxcolors=2)
        if image.mode == "1" or colors is not None:
            image.convert("1").save(output, format="PNG", optimize=True)
            return output.getvalue(), ".png", "image/png"
        image.convert("RGB").save(
            output,
            format="JPEG",
            quality=85,
            optimize=True,
            progressive=True,
        )
        return output.getvalue(), ".jpg", "image/jpeg"
    except (OSError, ValueError) as exc:
        raise DjvuReadError("Unable to encode a DjVu page image: {}".format(exc)) from exc


def _scale_words(
    words: Sequence[_WordBox],
    source_width: float,
    source_height: float,
    target_width: float,
    target_height: float,
) -> Tuple[_WordBox, ...]:
    if source_width <= 0 or source_height <= 0:
        return ()
    x_scale = target_width / source_width
    y_scale = target_height / source_height
    return tuple(
        _WordBox(
            word.text,
            word.left * x_scale,
            word.top * y_scale,
            word.width * x_scale,
            word.height * y_scale,
        )
        for word in words
    )


def _page_xhtml(
    language: str,
    page_number: int,
    asset_filename: str,
    width: int,
    height: int,
    words: Sequence[_WordBox],
    page_id: str,
) -> bytes:
    ET.register_namespace("", _XHTML_NS)
    root = ET.Element(
        _qualified(_XHTML_NS, "html"),
        {"lang": language, _qualified(_XML_NS, "lang"): language},
    )
    head = ET.SubElement(root, _qualified(_XHTML_NS, "head"))
    ET.SubElement(head, _qualified(_XHTML_NS, "title")).text = "Page {}".format(page_number)
    ET.SubElement(
        head,
        _qualified(_XHTML_NS, "meta"),
        {"name": "viewport", "content": "width={},height={}".format(width, height)},
    )
    ET.SubElement(
        head,
        _qualified(_XHTML_NS, "link"),
        {"rel": "stylesheet", "type": "text/css", "href": "../styles/book.css"},
    )
    body = ET.SubElement(root, _qualified(_XHTML_NS, "body"), {"class": "fixed-page"})
    container = ET.SubElement(
        body,
        _qualified(_XHTML_NS, "div"),
        {
            "class": "facsimile",
            "id": page_id,
            "style": "width:{}px;height:{}px".format(width, height),
        },
    )
    ET.SubElement(
        container,
        _qualified(_XHTML_NS, "img"),
        {
            "class": "facsimile-image",
            "src": "../{}".format(asset_filename),
            "alt": "",
            "aria-hidden": "true",
            "width": str(width),
            "height": str(height),
        },
    )
    text_layer = ET.SubElement(
        container,
        _qualified(_XHTML_NS, "div"),
        {"class": "text-layer", "aria-label": "Text of page {}".format(page_number)},
    )
    for word in words:
        style = (
            "left:{:.2f}px;top:{:.2f}px;width:{:.2f}px;height:{:.2f}px;font-size:{:.2f}px"
        ).format(
            word.left,
            word.top,
            word.width,
            word.height,
            max(1.0, word.height),
        )
        ET.SubElement(
            text_layer,
            _qualified(_XHTML_NS, "span"),
            {"style": style},
        ).text = _xml_text(word.text)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _xml_text(value: str) -> str:
    return "".join(
        character
        for character in str(value)
        if character in "\t\n\r"
        or 0x20 <= ord(character) <= 0xD7FF
        or 0xE000 <= ord(character) <= 0xFFFD
        or 0x10000 <= ord(character) <= 0x10FFFF
    )


def _qualified(namespace: str, name: str) -> str:
    return "{{{}}}{}".format(namespace, name)
