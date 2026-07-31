import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from pdf2epub import (
    ConversionOptions,
    DocumentReadError,
    NoExtractableTextError,
    OcrError,
    convert_document,
    convert_pdf,
)
from pdf2epub.djvu import (
    _decode_zone_text,
    _DjvuTools,
    _line_boxes_to_text_lines,
    _LineBox,
    _native_lines,
    _native_words,
    _repair_russian_ocr_blocks,
    _repair_russian_ocr_text,
    _WordBox,
    _xml_text,
    build_djvu_publication,
)
from pdf2epub.epub import write_publication
from pdf2epub.models import (
    EpubResource,
    ExtractedDocument,
    ExtractedPage,
    NavigationEntry,
    PreparedPublication,
    PublicationMetadata,
    RenderedSection,
    TextLine,
)
from pdf2epub.ocr import OcrWord, _resolve_ocr_language, recognize_image
from pdf2epub.structure import detect_blocks
from pdf2epub.word import (
    WordSource,
    _sanitize_element,
    build_word_publication,
    extract_word_document,
)

from .docx_factory import write_docx
from .pdf_factory import write_pdf

_XHTML_NS = "http://www.w3.org/1999/xhtml"
_OPF_NS = "http://www.idpf.org/2007/opf"
_EPUB_NS = "http://www.idpf.org/2007/ops"


def test_mixed_pdf_ocrs_only_the_text_empty_content_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = write_pdf(
        tmp_path / "mixed.pdf",
        [
            [("Native text.", 12, 72, 720, "regular")],
            [],
        ],
        image_only=True,
    )

    def recognize(_path, pages, indexes, **_kwargs):
        assert indexes == [1]
        pages[1] = pages[1].__class__(
            2,
            pages[1].width,
            pages[1].height,
            (TextLine("Recognized paragraph.", 2),),
        )
        return 1

    monkeypatch.setattr("pdf2epub.pdf._ocr_pdf_pages", recognize)

    result = convert_pdf(pdf)

    assert result.ocr_page_count == 1
    assert result.page_count == 2
    with zipfile.ZipFile(result.output_path) as archive:
        content = archive.read("EPUB/text/content.xhtml")
        assert b"Native text." in content
        assert b"Recognized paragraph." in content


def test_pdf_no_ocr_preserves_previous_textless_error(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "scan.pdf", [[]], image_only=True)

    with pytest.raises(NoExtractableTextError, match="OCR is disabled"):
        convert_pdf(pdf, options=ConversionOptions(ocr_enabled=False))


def test_ocr_discards_low_confidence_and_punctuation_only_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pdf2epub.ocr._find_tesseract", lambda: "tesseract")
    monkeypatch.setattr(
        "pdf2epub.ocr._resolve_ocr_language",
        lambda *_args, **_kwargs: "eng",
    )
    monkeypatch.setattr(
        "pdf2epub.ocr._run_tesseract",
        lambda *_args: [
            OcrWord("GIBBERISH", 10, 10, 50, 10, 1, 1, 1, 5.0),
            OcrWord("-", 65, 10, 5, 10, 1, 1, 1, 99.0),
            OcrWord("Readable", 75, 10, 50, 10, 1, 1, 1, 90.0),
        ],
    )

    result = recognize_image(
        Image.new("RGB", (200, 100), "white"),
        1,
        200,
        100,
        requested_language=None,
        publication_language="en",
        warnings=[],
    )

    assert [word.text for word in result.words] == ["Readable"]
    assert [line.text for line in result.lines] == ["Readable"]


def test_ocr_language_inference_and_explicit_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pdf2epub.ocr._available_languages",
        lambda _command: {"eng", "rus"},
    )

    assert (
        _resolve_ocr_language(
            "tesseract",
            requested_language=None,
            publication_language="ru-RU",
            warnings=[],
        )
        == "rus"
    )
    assert (
        _resolve_ocr_language(
            "tesseract",
            requested_language="eng+rus",
            publication_language=None,
            warnings=[],
        )
        == "eng+rus"
    )
    with pytest.raises(OcrError, match="not installed"):
        _resolve_ocr_language(
            "tesseract",
            requested_language="deu",
            publication_language=None,
            warnings=[],
        )


def test_word_publication_preserves_semantics_images_and_nested_navigation(
    tmp_path: Path,
) -> None:
    source = WordSource(
        elements=(
            b"<h1>Chapter One</h1>",
            b"<h2>Topic</h2>",
            b"<p>Some <strong>bold</strong> text.</p>",
            b'<p><a href="https://example.com">Link</a></p>',
            b"<table><tr><th>Head</th></tr><tr><td>Cell</td></tr></table>",
            b'<figure><img src="../images/word-image-001.png" alt="Diagram"/></figure>',
        ),
        resources=(EpubResource("images/word-image-001.png", "image/png", b"png"),),
        title="Word Title",
        author="Word Author",
        language="en",
        warnings=(),
    )
    metadata = PublicationMetadata("Word Title", "Word Author", "en", "urn:uuid:word")
    publication = build_word_publication(source, metadata)
    output = tmp_path / "word.epub"

    write_publication(output, metadata, publication, overwrite=False)

    assert publication.navigation[0].children[0].title == "Topic"
    with zipfile.ZipFile(output) as archive:
        chapter = ET.fromstring(archive.read("EPUB/text/chapter-001.xhtml"))
        assert chapter.find(f".//{{{_XHTML_NS}}}strong") is not None
        assert chapter.find(f".//{{{_XHTML_NS}}}table") is not None
        assert archive.read("EPUB/images/word-image-001.png") == b"png"
        nav = ET.fromstring(archive.read("EPUB/nav.xhtml"))
        assert "Topic" in "".join(nav.itertext())


def test_word_sanitizer_removes_dangerous_links_and_elements() -> None:
    root = ET.fromstring(
        '<div onclick="bad()"><a href="javascript:bad()">Unsafe</a>'
        "<script>bad()</script><p>Safe</p></div>"
    )

    sanitized = _sanitize_element(root)

    assert sanitized is not None
    assert "onclick" not in sanitized.attrib
    link = sanitized.find("a")
    assert link is not None and "href" not in link.attrib
    assert sanitized.find("script") is None
    assert sanitized.find("p") is not None


def test_convert_document_dispatches_docx_and_reports_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docx = tmp_path / "book.docx"
    docx.write_bytes(b"fixture")
    source = WordSource(
        (b"<p>Word text.</p>",),
        (EpubResource("images/picture.png", "image/png", b"image"),),
        "Book",
        "Author",
        "en",
        (),
    )
    monkeypatch.setattr("pdf2epub.word.extract_word_document", lambda _path: source)

    result = convert_document(docx)

    assert result.source_format == "docx"
    assert result.image_count == 1
    assert result.chapter_count == 1


def test_real_docx_conversion_preserves_metadata_and_semantics(tmp_path: Path) -> None:
    docx = write_docx(tmp_path / "semantic.docx")

    result = convert_document(docx)

    assert result.title == "DOCX Title"
    assert result.author == "DOCX Author"
    assert result.language == "en"
    assert result.chapter_count == 1
    assert result.image_count == 1
    with zipfile.ZipFile(result.output_path) as archive:
        chapter = ET.fromstring(archive.read("EPUB/text/chapter-001.xhtml"))
        assert chapter.find(f".//{{{_XHTML_NS}}}strong") is not None
        assert chapter.find(f".//{{{_XHTML_NS}}}table") is not None
        link = chapter.find(f".//{{{_XHTML_NS}}}a")
        assert link is not None and link.attrib["href"] == "https://example.com"
        image = chapter.find(f".//{{{_XHTML_NS}}}img")
        assert image is not None and image.attrib["alt"] == "A tiny diagram"
        assert archive.read("EPUB/images/word-image-001.png").startswith(b"\x89PNG")


def test_legacy_doc_uses_an_isolated_libreoffice_profile_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = tmp_path / "legacy.doc"
    legacy.write_bytes(b"fixture")
    expected = WordSource((b"<p>Converted.</p>",), (), None, None, None, ())
    temporary_root = None

    monkeypatch.setattr("pdf2epub.word._find_soffice", lambda: "/tools/soffice")
    monkeypatch.setattr("pdf2epub.word._extract_docx", lambda _path: expected)

    def run(command, **kwargs):
        nonlocal temporary_root
        profile_argument = next(
            argument for argument in command if argument.startswith("-env:UserInstallation=")
        )
        assert profile_argument.startswith("-env:UserInstallation=file:")
        output_directory = Path(command[command.index("--outdir") + 1])
        temporary_root = output_directory.parent
        assert kwargs["env"]["XDG_CACHE_HOME"] == str(temporary_root / "cache")
        (output_directory / "legacy.docx").write_bytes(b"converted")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pdf2epub.word.subprocess.run", run)

    assert extract_word_document(legacy) is expected
    assert temporary_root is not None and not temporary_root.exists()


def test_legacy_doc_conversion_failure_leaves_no_temporary_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = tmp_path / "legacy.doc"
    legacy.write_bytes(b"fixture")
    temporary_root = None

    monkeypatch.setattr("pdf2epub.word._find_soffice", lambda: "/tools/soffice")

    def fail(command, **_kwargs):
        nonlocal temporary_root
        output_directory = Path(command[command.index("--outdir") + 1])
        temporary_root = output_directory.parent
        return subprocess.CompletedProcess(command, 1, "", "conversion failed")

    monkeypatch.setattr("pdf2epub.word.subprocess.run", fail)

    with pytest.raises(DocumentReadError, match="conversion failed"):
        extract_word_document(legacy)
    assert temporary_root is not None and not temporary_root.exists()


def test_djvu_builds_fixed_layout_facsimile_and_page_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    djvu = tmp_path / "scan.djvu"
    djvu.write_bytes(b"fixture")
    monkeypatch.setattr(
        "pdf2epub.djvu._find_tools",
        lambda: _DjvuTools("djvused", "djvutxt", "ddjvu"),
    )
    monkeypatch.setattr("pdf2epub.djvu._page_count", lambda _tools, _path: 2)
    monkeypatch.setattr(
        "pdf2epub.djvu._native_words",
        lambda _tools, _path, page: (
            (_WordBox("Page{}".format(page), 10, 10, 40, 12),),
            (100.0, 100.0),
        ),
    )
    monkeypatch.setattr(
        "pdf2epub.djvu._render_page",
        lambda *_args: Image.new("1", (100, 100), color=1),
    )
    metadata = PublicationMetadata("Scan", None, "en", "urn:uuid:djvu")

    publication = build_djvu_publication(
        djvu,
        metadata,
        ocr_enabled=True,
        ocr_language=None,
        facsimile=True,
    )
    output = tmp_path / "scan.epub"
    write_publication(output, metadata, publication, overwrite=False)

    assert publication.fixed_layout is True
    assert len(publication.sections) == 2
    assert len(publication.page_list) == 2
    with zipfile.ZipFile(output) as archive:
        package = ET.fromstring(archive.read("EPUB/package.opf"))
        rendition = package.find(f".//{{{_OPF_NS}}}meta[@property='rendition:layout']")
        assert rendition is not None and rendition.text == "pre-paginated"
        page = ET.fromstring(archive.read("EPUB/pages/page-0001.xhtml"))
        image = page.find(f".//{{{_XHTML_NS}}}img")
        span = page.find(f".//{{{_XHTML_NS}}}span")
        assert image is not None
        assert span is not None and span.text == "Page1"
        nav = ET.fromstring(archive.read("EPUB/nav.xhtml"))
        page_nav = nav.find(f".//{{{_XHTML_NS}}}nav[@{{{_EPUB_NS}}}type='page-list']")
        assert page_nav is not None


def test_djvu_defaults_to_reflowable_text_with_visual_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    djvu = tmp_path / "book.djvu"
    djvu.write_bytes(b"fixture")
    monkeypatch.setattr(
        "pdf2epub.djvu._find_tools",
        lambda: _DjvuTools("djvused", "djvutxt", "ddjvu"),
    )
    monkeypatch.setattr("pdf2epub.djvu._page_count", lambda _tools, _path: 2)
    monkeypatch.setattr(
        "pdf2epub.djvu._native_lines",
        lambda _tools, _path, page: (
            (
                _LineBox(
                    "INTRODUCTION"
                    if page == 1
                    else "This is semantic text from source page {}.".format(page),
                    10,
                    60,
                    80,
                    10,
                ),
            ),
            (100.0, 100.0),
        ),
    )
    monkeypatch.setattr(
        "pdf2epub.djvu._background_visual",
        lambda _tools, _path, page, *_args: (
            Image.new("RGB", (40, 30), "navy") if page == 1 else None
        ),
    )
    metadata = PublicationMetadata("Book", None, "en", "urn:uuid:djvu-reflow")

    publication = build_djvu_publication(
        djvu,
        metadata,
        ocr_enabled=True,
        ocr_language=None,
    )
    output = tmp_path / "book.epub"
    write_publication(output, metadata, publication, overwrite=False)

    assert publication.fixed_layout is False
    assert publication.page_count == 2
    assert len(publication.resources) == 1
    with zipfile.ZipFile(output) as archive:
        package = ET.fromstring(archive.read("EPUB/package.opf"))
        rendition = package.find(f".//{{{_OPF_NS}}}meta[@property='rendition:layout']")
        assert rendition is None
        contents = [
            ET.fromstring(archive.read("EPUB/{}".format(section.filename)))
            for section in publication.sections
        ]
        assert any(content.find(f".//{{{_XHTML_NS}}}img") is not None for content in contents)
        text = " ".join(value for content in contents for value in content.itertext())
        assert "INTRODUCTION" in text
        assert "semantic text from source page 2" in text


def test_djvu_early_facsimiles_suppress_bad_text_and_choose_real_cover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    djvu = tmp_path / "front-matter.djvu"
    djvu.write_bytes(b"fixture")
    legal = Image.new("RGB", (200, 300), "white")
    legal.paste("black", (80, 50, 120, 180))
    cover = Image.new("RGB", (200, 300), (10, 40, 180))
    for x in range(cover.width):
        cover.paste((10 + x // 3, 40, 180), (x, 0, x + 1, cover.height))
    monkeypatch.setattr(
        "pdf2epub.djvu._find_tools",
        lambda: _DjvuTools("djvused", "djvutxt", "ddjvu"),
    )
    monkeypatch.setattr("pdf2epub.djvu._page_count", lambda _tools, _path: 3)
    monkeypatch.setattr(
        "pdf2epub.djvu._native_lines",
        lambda _tools, _path, page: (
            (
                _LineBox(
                    {1: "LEGAL NOTICE", 2: "BAD COVER OCR", 3: "INTRODUCTION"}[page],
                    10,
                    60,
                    80,
                    10,
                ),
            ),
            (100.0, 100.0),
        ),
    )
    monkeypatch.setattr(
        "pdf2epub.djvu._background_visual",
        lambda _tools, _path, page, *_args: {1: legal, 2: cover, 3: None}[page],
    )
    monkeypatch.setattr(
        "pdf2epub.djvu._render_page",
        lambda _tools, _path, page, *_args: {1: legal, 2: cover}[page],
    )
    metadata = PublicationMetadata("Book", None, "en", "urn:uuid:front")

    publication = build_djvu_publication(
        djvu,
        metadata,
        ocr_enabled=True,
        ocr_language=None,
    )
    output = tmp_path / "front-matter.epub"
    write_publication(output, metadata, publication, overwrite=False)

    cover_resources = [
        resource for resource in publication.resources if resource.properties == "cover-image"
    ]
    assert [resource.filename for resource in cover_resources] == ["images/djvu-visual-0002.jpg"]
    with zipfile.ZipFile(output) as archive:
        preface = ET.fromstring(archive.read("EPUB/text/preface.xhtml"))
        assert len(preface.findall(f".//{{{_XHTML_NS}}}img")) == 2
        assert len(preface.findall(f".//{{{_XHTML_NS}}}figure[@class='front-matter-page']")) == 2
        text = " ".join(preface.itertext())
        assert "LEGAL NOTICE" not in text
        assert "BAD COVER OCR" not in text


def test_djvu_native_coordinates_are_relative_to_a_cropped_page_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = (
        '(page 400 500 1400 2000 (line 420 1800 900 1850 "Line text") '
        '(word 420 1800 500 1850 "Word"))'
    )
    monkeypatch.setattr(
        "pdf2epub.djvu.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, output, ""),
    )
    source = tmp_path / "crop.djvu"
    tools = _DjvuTools("djvused", "djvutxt", "ddjvu")

    lines, line_size = _native_lines(tools, source, 1)
    words, word_size = _native_words(tools, source, 1)

    assert line_size == word_size == (1000.0, 1500.0)
    assert (lines[0].left, lines[0].bottom) == (20.0, 1300.0)
    assert (words[0].left, words[0].top) == (20.0, 150.0)


def test_djvu_text_removes_xml_illegal_control_characters() -> None:
    assert _xml_text("signals\x01, text") == "signals, text"


def test_djvu_paragraphs_join_scan_lines_and_discretionary_hyphens() -> None:
    wrapped = _decode_zone_text(r'"«резуль\037 "')
    lexical = _decode_zone_text(r'"человек\037человек"')
    boxes = (
        _LineBox("Проблема выглядит как {}".format(wrapped), 100, 300, 800, 40),
        _LineBox("тирующая» тема исследования и обсуждения", 50, 250, 850, 40),
        _LineBox("в приведенном научном тексте.", 52, 200, 700, 40),
        _LineBox("Следующий абзац начинается с отступа.", 100, 120, 800, 40),
    )
    lines = _line_boxes_to_text_lines(boxes, 1, 1000, 400)
    document = ExtractedDocument(
        (ExtractedPage(1, 1000, 400, lines),),
        None,
        None,
        "ru",
        (),
        (),
    )

    blocks = detect_blocks(document)

    assert lexical == "человек-человек"
    assert [block.kind for block in blocks] == ["paragraph", "paragraph"]
    assert "«результирующая»" in blocks[0].text
    assert "\u00ad" not in blocks[0].text


def test_djvu_repairs_dictionary_backed_mixed_script_russian_ocr() -> None:
    import pymorphy3

    text = (
        "как и пСИХОЛО2ИИ, наших переживаний и Ор2анизация их. "
        "Объект MHorocтopoHHero влияния, который ВЫС1Упает в роли "
        "KaTeroрии. Осознавая всю 2Лубину и MH0202ранность, посредством "
        "УМеНЬшения числа ИlЩивидов."
    )

    repaired, count = _repair_russian_ocr_text(
        text,
        pymorphy3.MorphAnalyzer(),
    )

    assert repaired == (
        "как и психологии, наших переживаний и организация их. "
        "Объект многостороннего влияния, который выступает в роли "
        "категории. Осознавая всю глубину и многогранность, посредством "
        "уменьшения числа индивидов."
    )
    assert count == 9


def test_djvu_repairs_corrupt_chapter_display_but_preserves_latin_text() -> None:
    import pymorphy3

    morphology = pymorphy3.MorphAnalyzer()

    label, _ = _repair_russian_ocr_text("rЛQВQ 1", morphology)
    title, _ = _repair_russian_ocr_text(
        "С-ЩНОСТЬ, ВИДЫ, МОДЕЛЬ И МЕХАНИ3МЫ",
        morphology,
    )
    latin, count = _repair_russian_ocr_text(
        "НЛП, NLP, model A/B, ISO 9001, COVID-19 and Word2Vec",
        morphology,
    )

    assert label == "ГЛАВА 1"
    assert title == "СУЩНОСТЬ, ВИДЫ, МОДЕЛЬ И МЕХАНИЗМЫ"
    assert latin == "НЛП, NLP, model A/B, ISO 9001, COVID-19 and Word2Vec"
    assert count == 0


def test_djvu_repairs_mixed_word_created_by_joining_scan_lines() -> None:
    import pymorphy3

    boxes = (
        _LineBox("Люди, KOTO\u00ad", 50, 100, 500, 40),
        _LineBox("рые читают книги.", 50, 50, 500, 40),
    )
    lines = _line_boxes_to_text_lines(boxes, 1, 600, 200)
    document = ExtractedDocument(
        (ExtractedPage(1, 600, 200, lines),),
        None,
        None,
        "ru",
        (),
        (),
    )

    blocks, count = _repair_russian_ocr_blocks(
        detect_blocks(document),
        pymorphy3.MorphAnalyzer(),
    )

    assert blocks[0].text == "Люди, которые читают книги."
    assert count == 1


def test_djvu_repairs_additional_recurring_sheynov_encodings() -> None:
    import pymorphy3

    repaired, count = _repair_russian_ocr_text(
        "В проuессе этоzо cкpbIToro влияния с1удентыI смотрят в 2Jlаза и читают кнuzи.",
        pymorphy3.MorphAnalyzer(),
    )

    assert repaired == (
        "В процессе этого скрытого влияния студенты смотрят в глаза и читают книги."
    )
    assert count == 6


def test_djvu_repairs_dictionary_backed_pure_cyrillic_confusions() -> None:
    import pymorphy3

    repaired, count = _repair_russian_ocr_text(
        "Вопрос о сушности системообразуюшая категория рчевидна в "
        "моносрафии, а 110сле нее — в статье.",
        pymorphy3.MorphAnalyzer(),
    )

    assert repaired == (
        "Вопрос о сущности системообразующая категория очевидна в "
        "монографии, а после нее — в статье."
    )
    assert count == 5

    split_word, split_count = _repair_russian_ocr_text(
        "С этой целью в MOHocpaфии предложена система.",
        pymorphy3.MorphAnalyzer(),
    )
    assert split_word == "С этой целью в монографии предложена система."
    assert split_count == 1


def test_rich_writer_rejects_a_missing_embedded_image(tmp_path: Path) -> None:
    metadata = PublicationMetadata("Title", None, "en", "urn:uuid:missing")
    section = RenderedSection(
        "text/content.xhtml",
        "Title",
        (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Title</title></head>'
            b'<body><img src="../images/missing.png" alt=""/></body></html>'
        ),
    )
    publication = PreparedPublication(
        (section,),
        navigation=(NavigationEntry("Title", "text/content.xhtml"),),
    )

    with pytest.raises(Exception, match="missing resource"):
        write_publication(tmp_path / "missing.epub", metadata, publication, overwrite=False)
