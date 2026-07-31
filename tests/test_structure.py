from typing import Optional, Sequence

from pdf2epub.models import ExtractedDocument, ExtractedPage, TextLine
from pdf2epub.structure import detect_blocks


def test_layout_signals_detect_heading_and_subtitle() -> None:
    document = _document(
        [
            _line(
                "CHAPTER ONE",
                size=24,
                y=720,
                centered=True,
                page_start=True,
                blank_before=True,
            ),
            _line("A Subtitle", size=14, y=680, centered=True, italic=True),
            _line("This is the body of the chapter.", size=12, y=640),
        ]
    )

    blocks = detect_blocks(document)

    assert [block.kind for block in blocks] == ["heading", "subtitle", "paragraph"]
    assert blocks[0].text == "CHAPTER ONE"


def test_uppercase_word_and_numeric_content_are_not_discarded() -> None:
    document = _document(
        [
            _line("NASA", page_start=True, blank_before=True),
            _line("1984"),
            _line("Both belong to the ordinary paragraph.", blank_after=True),
        ]
    )

    blocks = detect_blocks(document)

    assert [block.kind for block in blocks] == ["paragraph"]
    assert blocks[0].text == "NASA 1984 Both belong to the ordinary paragraph."


def test_outline_title_is_a_heading_without_font_evidence() -> None:
    document = _document(
        [
            _line("Opening", page_start=True),
            _line("The first paragraph follows."),
        ],
        outline_titles=("Opening",),
    )

    blocks = detect_blocks(document)

    assert blocks[0].kind == "heading"
    assert blocks[0].text == "Opening"


def test_centered_uppercase_label_is_heading_even_when_smaller_than_body() -> None:
    document = _document(
        [
            _line(
                "INTRODUCTION",
                size=10,
                centered=True,
                page_start=True,
                blank_after=True,
            ),
            _line("The body text follows the centered label.", size=12),
        ]
    )

    blocks = detect_blocks(document)

    assert [block.kind for block in blocks] == ["heading", "paragraph"]


def test_consecutive_same_page_heading_lines_are_combined() -> None:
    document = _document(
        [
            _line(
                "A MULTI-",
                size=24,
                centered=True,
                page_start=True,
                blank_before=True,
            ),
            _line(
                "LINE HEADING",
                size=24,
                centered=True,
                blank_after=True,
            ),
            _line("The chapter starts here.", size=12),
        ]
    )

    blocks = detect_blocks(document)

    assert blocks[0].kind == "heading"
    assert blocks[0].text == "A MULTI-LINE HEADING"


def test_title_case_chapter_label_and_centered_title_are_combined() -> None:
    document = _document(
        [
            _line(
                "Глава 1",
                centered=True,
                page_start=True,
                blank_before=True,
            ),
            _line(
                "ГНОСТИЦИЗМ",
                centered=True,
                blank_after=True,
            ),
            _line(
                "Первый абзац главы заканчивается точкой.",
                blank_before=True,
            ),
            _line("Он продолжается на следующей строке."),
        ]
    )

    blocks = detect_blocks(document)

    assert [block.kind for block in blocks] == ["heading", "paragraph"]
    assert blocks[0].text == "Глава 1 ГНОСТИЦИЗМ"
    assert (
        blocks[1].text
        == "Первый абзац главы заканчивается точкой. Он продолжается на следующей строке."
    )


def test_chapter_note_label_before_a_numbered_note_is_not_a_heading() -> None:
    document = _document(
        [
            _line(
                "Глава 1",
                centered=True,
                page_start=True,
                blank_before=True,
            ),
            _line("1 Первая пронумерованная сноска продолжается здесь."),
        ]
    )

    blocks = detect_blocks(document)

    assert [block.kind for block in blocks] == ["paragraph"]


def test_hyphenated_line_wrap_has_no_inserted_space() -> None:
    document = _document(
        [
            _line("This is a well-", page_start=True),
            _line("known example."),
        ]
    )

    blocks = detect_blocks(document)

    assert blocks[0].text == "This is a well-known example."


def test_incomplete_paragraph_continues_across_pages() -> None:
    first = ExtractedPage(
        1,
        612,
        792,
        (_line("This paragraph continues", page=1, page_start=True, page_end=True),),
    )
    second = ExtractedPage(
        2,
        612,
        792,
        (_line("onto the following page.", page=2, page_start=True, page_end=True),),
    )
    document = ExtractedDocument(
        pages=(first, second),
        title=None,
        author=None,
        language=None,
        outline_titles=(),
        warnings=(),
    )

    blocks = detect_blocks(document)

    assert len(blocks) == 1
    assert blocks[0].text == "This paragraph continues onto the following page."


def test_repeated_headers_footers_and_page_numbers_are_removed() -> None:
    pages = []
    for number in range(1, 4):
        lines = (
            _line("RUNNING HEADER", page=number, y=770, page_start=True),
            _line("Body text for page {}.".format(number), page=number, y=650),
            _line("1984", page=number, y=600),
            _line(str(number), page=number, y=20, page_end=True),
        )
        pages.append(ExtractedPage(number, 612, 792, lines))
    document = ExtractedDocument(
        pages=tuple(pages),
        title=None,
        author=None,
        language=None,
        outline_titles=(),
        warnings=(),
    )

    text = " ".join(block.text for block in detect_blocks(document))

    assert "RUNNING HEADER" not in text
    assert "1984" in text
    assert "Body text for page" in text


def test_short_terminal_line_starts_a_new_paragraph() -> None:
    document = _document(
        [
            _line("A short paragraph.", page_start=True),
            _line(
                "The following paragraph is considerably longer than the first line.",
                blank_before=False,
            ),
        ]
    )

    blocks = detect_blocks(document)

    assert [block.text for block in blocks] == [
        "A short paragraph.",
        "The following paragraph is considerably longer than the first line.",
    ]


def test_indentation_starts_a_new_paragraph_without_font_evidence() -> None:
    document = _document(
        [
            _line(
                "The first paragraph has enough characters to establish the body margin",
                page_start=True,
                x=0,
            ),
            _line(
                "The indented paragraph also has enough characters for margin detection.",
                x=3,
            ),
        ]
    )

    blocks = detect_blocks(document)

    assert [block.text for block in blocks] == [
        "The first paragraph has enough characters to establish the body margin",
        "The indented paragraph also has enough characters for margin detection.",
    ]


def _document(
    lines: Sequence[TextLine],
    *,
    outline_titles: Sequence[str] = (),
) -> ExtractedDocument:
    return ExtractedDocument(
        pages=(ExtractedPage(1, 612, 792, tuple(lines)),),
        title=None,
        author=None,
        language=None,
        outline_titles=tuple(outline_titles),
        warnings=(),
    )


def _line(
    text: str,
    *,
    page: int = 1,
    size: Optional[float] = None,
    x: Optional[float] = None,
    y: Optional[float] = None,
    centered: bool = False,
    italic: bool = False,
    page_start: bool = False,
    page_end: bool = False,
    blank_before: bool = False,
    blank_after: bool = False,
) -> TextLine:
    return TextLine(
        text=text,
        page_number=page,
        x=x,
        y=y,
        font_size=size,
        italic=italic,
        centered=centered,
        page_start=page_start,
        page_end=page_end,
        blank_before=blank_before,
        blank_after=blank_after,
    )
