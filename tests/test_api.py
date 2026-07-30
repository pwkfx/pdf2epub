import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from pdf2epub import (
    ConversionOptions,
    EpubWriteError,
    InputFileError,
    NoExtractableTextError,
    PdfReadError,
    convert_pdf,
)
from pdf2epub.epub import write_epub
from pdf2epub.models import Block, PublicationMetadata, Section

from .pdf_factory import regular_lines, write_pdf

_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"


def test_conversion_creates_complete_epub3_archive(tmp_path: Path) -> None:
    pdf = write_pdf(
        tmp_path / "source.pdf",
        [
            [
                ("CHAPTER ONE", 24, 180, 720, "bold"),
                ("A Subtitle", 14, 230, 680, "italic"),
                ("First paragraph.", 12, 72, 640, "regular"),
            ],
            [
                ("CHAPTER TWO", 24, 180, 720, "bold"),
                ("Second paragraph.", 12, 72, 660, "regular"),
            ],
        ],
        metadata={"/Title": "PDF Title", "/Author": "PDF Author"},
        language="ru",
        outline_title="CHAPTER ONE",
    )

    result = convert_pdf(
        pdf,
        options=ConversionOptions(title="CLI Title", language="en"),
    )

    assert result.output_path == tmp_path / "source.epub"
    assert result.title == "CLI Title"
    assert result.author == "PDF Author"
    assert result.language == "en"
    assert result.chapter_count == 2
    assert re.fullmatch(r"urn:uuid:[0-9a-f-]{36}", result.identifier)

    with zipfile.ZipFile(result.output_path) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.testzip() is None
        expected = {
            "META-INF/container.xml",
            "EPUB/package.opf",
            "EPUB/nav.xhtml",
            "EPUB/toc.ncx",
            "EPUB/styles/book.css",
            "EPUB/text/chapter-001.xhtml",
            "EPUB/text/chapter-002.xhtml",
        }
        assert expected.issubset(names)

        package = ET.fromstring(archive.read("EPUB/package.opf"))
        assert package.attrib["version"] == "3.0"
        identifier = package.find(".//{{{}}}identifier".format(_DC_NS))
        assert identifier is not None
        assert identifier.text == result.identifier
        manifest_hrefs = {
            item.attrib["href"] for item in package.findall(".//{{{}}}item".format(_OPF_NS))
        }
        for href in manifest_hrefs:
            assert "EPUB/{}".format(href) in names
        assert result.identifier.encode() in archive.read("EPUB/toc.ncx")
        assert b"CLI Title" in archive.read("EPUB/package.opf")


def test_content_before_first_heading_becomes_preface(tmp_path: Path) -> None:
    pdf = write_pdf(
        tmp_path / "preface.pdf",
        [
            [
                ("Introductory text.", 12, 72, 740, "regular"),
                ("CHAPTER ONE", 24, 180, 680, "bold"),
                ("Chapter text.", 12, 72, 640, "regular"),
            ]
        ],
    )

    result = convert_pdf(pdf)

    with zipfile.ZipFile(result.output_path) as archive:
        assert "EPUB/text/preface.xhtml" in archive.namelist()
        assert "EPUB/text/chapter-001.xhtml" in archive.namelist()
        assert b"Preface" in archive.read("EPUB/nav.xhtml")


def test_no_heading_uses_single_content_document(tmp_path: Path) -> None:
    pdf = write_pdf(
        tmp_path / "plain.pdf",
        [regular_lines("An ordinary paragraph with no chapter heading.")],
    )

    result = convert_pdf(pdf)

    assert result.chapter_count == 1
    with zipfile.ZipFile(result.output_path) as archive:
        assert "EPUB/text/content.xhtml" in archive.namelist()


def test_filename_and_und_are_metadata_fallbacks(tmp_path: Path) -> None:
    pdf = write_pdf(
        tmp_path / "fallback-title.pdf",
        [regular_lines("An ordinary paragraph.")],
    )

    result = convert_pdf(pdf)

    assert result.title == "fallback-title"
    assert result.author is None
    assert result.language == "und"


def test_xml_special_characters_round_trip(tmp_path: Path) -> None:
    pdf = write_pdf(
        tmp_path / "escaping.pdf",
        [regular_lines("Body text with & and <angle> characters.")],
        metadata={"/Title": 'A <Book> & "Title"', "/Author": "One & Two"},
    )

    result = convert_pdf(pdf)

    with zipfile.ZipFile(result.output_path) as archive:
        package = ET.fromstring(archive.read("EPUB/package.opf"))
        title = package.find(f".//{{{_DC_NS}}}title")
        creator = package.find(f".//{{{_DC_NS}}}creator")
        assert title is not None and title.text == 'A <Book> & "Title"'
        assert creator is not None and creator.text == "One & Two"
        content = ET.fromstring(archive.read("EPUB/text/content.xhtml"))
        assert "Body text with & and <angle> characters." in "".join(content.itertext())


def test_default_collision_policy_adds_suffixes(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "book.pdf", [regular_lines("Text.")])
    (tmp_path / "book.epub").write_bytes(b"original")
    (tmp_path / "book-1.epub").write_bytes(b"previous")

    result = convert_pdf(pdf)

    assert result.output_path == tmp_path / "book-2.epub"
    assert (tmp_path / "book.epub").read_bytes() == b"original"


def test_overwrite_replaces_exact_path(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "book.pdf", [regular_lines("Text.")])
    output = tmp_path / "custom.epub"
    output.write_bytes(b"old")

    result = convert_pdf(
        pdf,
        output,
        options=ConversionOptions(overwrite=True),
    )

    assert result.output_path == output
    assert output.read_bytes()[:2] == b"PK"


def test_suffixless_explicit_output_gets_epub_extension(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "book.pdf", [regular_lines("Text.")])

    result = convert_pdf(pdf, tmp_path / "export")

    assert result.output_path == tmp_path / "export.epub"


def test_non_epub_output_is_rejected_without_touching_source(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "book.pdf", [regular_lines("Text.")])
    original = pdf.read_bytes()

    with pytest.raises(InputFileError, match=r"\.epub extension"):
        convert_pdf(
            pdf,
            pdf,
            options=ConversionOptions(overwrite=True),
        )

    assert pdf.read_bytes() == original


def test_invalid_pdf_language_falls_back_to_und_with_warning(tmp_path: Path) -> None:
    pdf = write_pdf(
        tmp_path / "book.pdf",
        [regular_lines("Text.")],
        language="not a valid language!",
    )

    result = convert_pdf(pdf)

    assert result.language == "und"
    assert "invalid" in result.warnings[0]


def test_invalid_language_override_is_rejected(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "book.pdf", [regular_lines("Text.")])

    with pytest.raises(InputFileError, match="BCP-47"):
        convert_pdf(pdf, options=ConversionOptions(language="not valid!"))


def test_missing_input_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputFileError, match="does not exist"):
        convert_pdf(tmp_path / "missing.pdf")


def test_missing_output_directory_is_rejected(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "book.pdf", [regular_lines("Text.")])

    with pytest.raises(InputFileError, match="Output directory"):
        convert_pdf(pdf, tmp_path / "missing" / "book.epub")


def test_malformed_pdf_has_focused_error(tmp_path: Path) -> None:
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a pdf")

    with pytest.raises(PdfReadError, match="Unable to read PDF"):
        convert_pdf(pdf)


def test_encrypted_pdf_is_rejected(tmp_path: Path) -> None:
    pdf = write_pdf(
        tmp_path / "encrypted.pdf",
        [regular_lines("Secret text.")],
        encrypted=True,
    )

    with pytest.raises(PdfReadError, match="Password-protected"):
        convert_pdf(pdf)


def test_text_empty_pdf_recommends_ocr(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "scan.pdf", [[]])

    with pytest.raises(NoExtractableTextError, match="OCR"):
        convert_pdf(pdf)


def test_write_failure_cleans_up_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = Section(
        "text/content.xhtml",
        "Title",
        (Block("paragraph", "Text."),),
        "content",
    )
    metadata = PublicationMetadata("Title", None, "und", "urn:uuid:test")

    class BrokenArchive:
        def __init__(self, *args, **kwargs) -> None:
            raise OSError("simulated failure")

    monkeypatch.setattr("pdf2epub.epub.zipfile.ZipFile", BrokenArchive)

    with pytest.raises(EpubWriteError, match="simulated failure"):
        write_epub(tmp_path / "failed.epub", metadata, (section,), overwrite=False)

    assert not (tmp_path / "failed.epub").exists()
    assert list(tmp_path.glob(".failed.epub.*.tmp")) == []
