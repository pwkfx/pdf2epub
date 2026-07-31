import os
import zipfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import pytest

from pdf2epub import (
    EpubReadError,
    EpubValidationError,
    EpubWriteError,
    InputFileError,
    RepairOptions,
    repair_epub,
)
from pdf2epub.repair import ValidationSummary

from .epub_factory import basic_xhtml, write_epub

_DC_NS = "http://purl.org/dc/elements/1.1/"
_OPF_NS = "http://www.idpf.org/2007/opf"
_XHTML_NS = "http://www.w3.org/1999/xhtml"


def _allow_epubcheck(
    monkeypatch: pytest.MonkeyPatch,
    *,
    before: Optional[ValidationSummary] = None,
    after: Optional[ValidationSummary] = None,
) -> None:
    before_summary = before or ValidationSummary(0, 7, 0, ())
    after_summary = after or ValidationSummary(0, 0, 0, ())
    calls = {"count": 0}

    monkeypatch.setattr(
        "pdf2epub.repair._resolve_epubcheck_command",
        lambda _options: ("fake-epubcheck",),
    )

    def validate(_command, _publication):
        calls["count"] += 1
        return before_summary if calls["count"] % 2 else after_summary

    monkeypatch.setattr("pdf2epub.repair._run_epubcheck", validate)


def test_minimal_repairs_preserve_publication_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    xhtml = basic_xhtml(
        '<div><span id="outer"><span id="inner"><p>Text '
        '<img src="image.jpg"/></p></span></span></div>'
        '<span id="rows"><tr id="orphan-1"><td>A</td></tr>'
        '<tr id="orphan-2"><td>B</td></tr></span>'
        '<table class="grid"><tr><td class="cell">'
        '<tr id="nested"><td>C</td></tr></td></tr></table>'
    )
    source = write_epub(
        tmp_path / "book.epub",
        xhtml,
        empty_guide=True,
        extra_entries={"OPS/image.jpg": b"unchanged-image-data"},
    )
    original_source = source.read_bytes()

    result = repair_epub(source)

    assert result.output_path == tmp_path / "book-fixed.epub"
    assert result.epub_version == "2.0"
    assert result.before_error_count == 7
    assert result.after_error_count == 0
    assert len(result.fixes) == 5
    assert source.read_bytes() == original_source

    with zipfile.ZipFile(result.output_path) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert archive.read("OPS/image.jpg") == b"unchanged-image-data"
        assert archive.read("OPS/style.css") == b"table.grid { border-collapse: collapse; }"

        package = ET.fromstring(archive.read("OPS/package.opf"))
        assert package.find(f".//{{{_OPF_NS}}}guide") is None
        assert package.find(f".//{{{_DC_NS}}}title").text == "Original Title"
        assert package.find(f".//{{{_DC_NS}}}creator").text == "Original Author"

        content = ET.fromstring(archive.read("OPS/book.xhtml"))
        assert content.find(f".//{{{_XHTML_NS}}}span") is None
        assert content.find(f".//{{{_XHTML_NS}}}img").attrib["alt"] == ""
        nested = content.find(f".//{{{_XHTML_NS}}}tr[@id='nested']")
        assert nested is not None
        parents = {child: parent for parent in content.iter() for child in parent}
        assert parents[nested].tag == f"{{{_XHTML_NS}}}table"
        assert [
            row.attrib["id"]
            for table in content.findall(f".//{{{_XHTML_NS}}}table")
            for row in list(table)
            if row.attrib.get("id", "").startswith("orphan-")
        ] == ["orphan-1", "orphan-2"]


def test_valid_epub3_produces_validated_copy_without_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = ValidationSummary(0, 0, 0, ())
    _allow_epubcheck(monkeypatch, before=clean, after=clean)
    source = write_epub(
        tmp_path / "clean.epub",
        basic_xhtml("<span>Valid EPUB 3 flow content.</span>"),
        version="3.0",
    )

    result = repair_epub(source)

    assert result.epub_version == "3.0"
    assert result.fixes == ("No structural changes were required; created a validated copy.",)
    with zipfile.ZipFile(result.output_path) as archive:
        assert archive.read("OPS/book.xhtml") == basic_xhtml(
            "<span>Valid EPUB 3 flow content.</span>"
        )


def test_full_repair_rebuilds_metadata_chapters_navigation_and_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    source = write_epub(
        tmp_path / "trading.epub",
        basic_xhtml(
            '<div class="paragraph">Артём Звёздин</div>'
            '<div class="paragraph"><strong>Биржа. Легко не будет.</strong></div>'
            '<div class="paragraph"><strong>Оглавление</strong></div>'
            '<div class="paragraph">Глава 1. Пример 6</div>'
            '<div class="paragraph"><strong>Благодарности</strong></div>'
            '<div class="paragraph">Спасибо.</div>'
            '<div class="paragraph"><strong>Введение</strong></div>'
            '<div class="paragraph">Прак-тически полезный текст.</div>'
            '<div class="paragraph"><strong>Глава 1. Пример</strong></div>'
            '<div class="paragraph">Тайна-ми рынка.</div>'
            '<div class="paragraph"><img src="chart.jpg"/></div>'
            '<div class="paragraph"><strong>Заключение</strong></div>'
            '<div class="paragraph">Итог.</div>'
        ),
        empty_guide=True,
        extra_entries={"OPS/chart.jpg": b"chart-bytes"},
        extra_manifest={"chart.jpg": "image/jpeg"},
    )

    result = repair_epub(source, options=RepairOptions(full_repair=True))

    assert result.output_path == tmp_path / "trading-rebuilt.epub"
    assert result.epub_version == "3.3"
    assert result.title == "Биржа. Легко не будет."
    assert result.author == "Артём Звёздин"
    assert result.language == "ru"
    assert result.chapter_count == 1
    assert not any(fix.startswith("Added empty alt") for fix in result.fixes)
    with zipfile.ZipFile(result.output_path) as archive:
        package = ET.fromstring(archive.read("OPS/package.opf"))
        assert package.attrib["version"] == "3.0"
        assert package.find(f".//{{{_DC_NS}}}title").text == result.title
        assert package.find(f".//{{{_DC_NS}}}creator").text == result.author
        nav_item = next(item for item in package.iter() if item.attrib.get("properties") == "nav")
        assert nav_item.attrib["href"] == "nav.xhtml"
        introduction = " ".join(ET.fromstring(archive.read("OPS/introduction.xhtml")).itertext())
        assert "Практически полезный текст." in introduction
        chapter = ET.fromstring(archive.read("OPS/chapter-01.xhtml"))
        chapter_text = " ".join(chapter.itertext())
        assert "Тайнами рынка." in chapter_text
        assert chapter.find(f".//{{{_XHTML_NS}}}h1").text == "Глава 1. Пример"
        image = chapter.find(f".//{{{_XHTML_NS}}}img")
        assert image is not None
        assert image.attrib["alt"].startswith("Иллюстрация 1")
        assert archive.read("OPS/chart.jpg") == b"chart-bytes"
        nav = ET.fromstring(archive.read("OPS/nav.xhtml"))
        nav_targets = [
            anchor.attrib["href"] for anchor in nav.iter() if anchor.tag == f"{{{_XHTML_NS}}}a"
        ]
        assert "chapter-01.xhtml" in nav_targets


def test_full_bregg_repair_does_not_split_on_same_named_subsection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    source = write_epub(
        tmp_path / "finance.epub",
        basic_xhtml(
            '<div class="title">НАСТОЛЬНАЯ КНИГА ФИНАНСОВОГО ДИРЕКТОРА</div>'
            '<p class="subtitle">ГЛАВА</p>'
            '<p class="subtitle">НАЛОГОВАЯ СТРАТЕГИЯ</p>'
            '<p class="subtitle">СЛИЯНИЯ И ПОГЛОЩЕНИЯ</p>'
            "<p>Налоговый подраздел.</p>"
            '<p class="subtitle">ГЛАВА</p>'
            '<p class="subtitle">СЛИЯНИЯ И ПОГЛОЩЕНИЯ</p>'
            "<p>Самостоятельная глава.</p>"
        ),
    )

    result = repair_epub(source, options=RepairOptions(full_repair=True))

    assert result.chapter_count == 2
    with zipfile.ZipFile(result.output_path) as archive:
        package = ET.fromstring(archive.read("OPS/package.opf"))
        manifest = {
            item.attrib.get("id"): item.attrib.get("href")
            for item in package.iter()
            if item.tag == f"{{{_OPF_NS}}}item"
        }
        spine = [
            manifest[itemref.attrib["idref"]]
            for itemref in package.iter()
            if itemref.tag == f"{{{_OPF_NS}}}itemref"
        ]
        assert spine.index("chapter-03.xhtml") < spine.index("chapter-21.xhtml")
        chapter_three = " ".join(ET.fromstring(archive.read("OPS/chapter-03.xhtml")).itertext())
        assert "Налоговый подраздел." in chapter_three
        assert "Самостоятельная глава." not in chapter_three


def test_full_bregg_repair_separates_run_in_topics_and_page_continuations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    source = write_epub(
        tmp_path / "topics.epub",
        basic_xhtml(
            '<div class="title">НАСТОЛЬНАЯ КНИГА ФИНАНСОВОГО ДИРЕКТОРА</div>'
            '<p class="subtitle">ГЛАВА</p>'
            '<p class="subtitle">МЕСТО ФИНАНСОВОГО ДИРЕКТОРА В КОРПОРАЦИИ</p>'
            '<p class="subtitle">ОБЗОР ПРОЦЕССА УПРАВЛЕНЧЕСКИХ ИЗМЕНЕНИЙ</p>'
            '<p class="p">— <em>Валютный риск.</em> Основной текст.</p>'
            '<p class="p"><em>Неблагоприятные изменения.</em> Директор должен иметь</p>'
            '<p class="p">представление о законопроектах.</p>'
            '<p class="p"><em>о Перестановки в службах.</em> Следующий текст.</p>'
            '<p class="p"><em>Срыв контрактов</em>. Первый текст. '
            "<em>Чрезвычайные обстоятельства.</em> Второй текст.</p>"
            '<p class="p">— Совершенствование <em>персонала.</em> Люди важны.</p>'
        ),
    )

    result = repair_epub(source, options=RepairOptions(full_repair=True))

    assert any("run-in topic labels" in fix for fix in result.fixes)
    assert any("topic paragraphs split" in fix for fix in result.fixes)
    with zipfile.ZipFile(result.output_path) as archive:
        chapter = ET.fromstring(archive.read("OPS/chapter-01.xhtml"))
        body = chapter.find(f".//{{{_XHTML_NS}}}body")
        assert body is not None
        children = list(body)
        headings = [
            " ".join(element.itertext())
            for element in children
            if element.tag == f"{{{_XHTML_NS}}}h3"
        ]
        assert headings == [
            "Валютный риск",
            "Неблагоприятные изменения",
            "Перестановки в службах",
            "Срыв контрактов",
            "Чрезвычайные обстоятельства",
            "Совершенствование персонала",
        ]
        adverse_index = next(
            index
            for index, element in enumerate(children)
            if element.tag == f"{{{_XHTML_NS}}}h3" and element.text == "Неблагоприятные изменения"
        )
        adverse_body = " ".join(children[adverse_index + 1].itertext())
        assert "должен иметь представление о законопроектах" in adverse_body

        nav = ET.fromstring(archive.read("OPS/nav.xhtml"))
        nav_text = " ".join(nav.itertext())
        assert "Валютный риск" not in nav_text
        assert "Чрезвычайные обстоятельства" not in nav_text


def test_full_bregg_repair_normalizes_ocr_bullets_as_semantic_lists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    source = write_epub(
        tmp_path / "bullets.epub",
        basic_xhtml(
            '<div class="title">НАСТОЛЬНАЯ КНИГА ФИНАНСОВОГО ДИРЕКТОРА</div>'
            "<p>• Первый вопрос?</p>"
            "<p>· Второй вопрос?</p>"
            '<p id="registered">® <strong>Третий вопрос?</strong></p>'
            "<p>© Четвертый вопрос?</p>"
            "<p>■ Пятый вопрос?</p>"
            "<p>» Шестой вопрос?</p>"
            "<p>* Седьмой вопрос?</p>"
            "<p>о Восьмой вопрос?</p>"
            "<p>.Девятый вопрос?</p>"
            "<p>о доходе и активах говорится в обычном предложении.</p>"
            "<p>+ 5 — арифметическое выражение, а не список.</p>"
        ),
    )

    result = repair_epub(source, options=RepairOptions(full_repair=True))

    assert any(
        "Normalized 9 OCR bullet markers into 1 semantic XHTML lists." in fix
        for fix in result.fixes
    )
    with zipfile.ZipFile(result.output_path) as archive:
        frontmatter = ET.fromstring(archive.read("OPS/frontmatter.xhtml"))
        lists = frontmatter.findall(f".//{{{_XHTML_NS}}}ul[@class='normalized-list']")
        assert len(lists) == 1
        items = lists[0].findall(f"{{{_XHTML_NS}}}li")
        assert [" ".join(item.itertext()) for item in items] == [
            "Первый вопрос?",
            "Второй вопрос?",
            "Третий вопрос?",
            "Четвертый вопрос?",
            "Пятый вопрос?",
            "Шестой вопрос?",
            "Седьмой вопрос?",
            "Восьмой вопрос?",
            "Девятый вопрос?",
        ]
        assert items[2].attrib["id"] == "registered"
        assert items[2].find(f"{{{_XHTML_NS}}}strong") is not None
        prose = [
            " ".join(element.itertext()) for element in frontmatter.findall(f".//{{{_XHTML_NS}}}p")
        ]
        assert "о доходе и активах говорится в обычном предложении." in prose
        assert "+ 5 — арифметическое выражение, а не список." in prose
        css = archive.read("OPS/rebuild.css")
        assert b"list-style-type: disc" in css


def test_default_collision_and_explicit_overwrite_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    source = write_epub(tmp_path / "book.epub", basic_xhtml())
    (tmp_path / "book-fixed.epub").write_bytes(b"existing")

    suffixed = repair_epub(source)
    overwritten = repair_epub(
        source,
        tmp_path / "custom.epub",
        options=RepairOptions(overwrite=True),
    )

    assert suffixed.output_path == tmp_path / "book-fixed-1.epub"
    assert (tmp_path / "book-fixed.epub").read_bytes() == b"existing"
    assert overwritten.output_path == tmp_path / "custom.epub"
    assert overwritten.output_path.read_bytes().startswith(b"PK")


def test_source_path_is_rejected_even_with_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    source = write_epub(tmp_path / "book.epub", basic_xhtml())
    original = source.read_bytes()

    with pytest.raises(InputFileError, match="must not replace"):
        repair_epub(source, source, options=RepairOptions(overwrite=True))

    assert source.read_bytes() == original


def test_hard_link_to_source_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    source = write_epub(tmp_path / "book.epub", basic_xhtml())
    linked_output = tmp_path / "linked.epub"
    os.link(source, linked_output)

    with pytest.raises(InputFileError, match="must not replace"):
        repair_epub(
            source,
            linked_output,
            options=RepairOptions(overwrite=True),
        )


def test_suffixless_explicit_output_gets_epub_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    source = write_epub(tmp_path / "book.epub", basic_xhtml())

    result = repair_epub(source, tmp_path / "export")

    assert result.output_path == tmp_path / "export.epub"


def test_epubcheck_warnings_are_allowed_and_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warning = "WARNING(OPF-001): Example warning [2 occurrences]"
    _allow_epubcheck(
        monkeypatch,
        after=ValidationSummary(0, 0, 2, (warning,)),
    )
    source = write_epub(tmp_path / "warning.epub", basic_xhtml())

    result = repair_epub(source)

    assert result.after_warning_count == 2
    assert result.warnings == (warning,)
    assert result.output_path.exists()


def test_unresolved_errors_leave_no_output_or_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remaining = "ERROR(RSC-005): Unsupported defect [1 occurrence]"
    _allow_epubcheck(
        monkeypatch,
        after=ValidationSummary(0, 1, 0, (remaining,)),
    )
    source = write_epub(tmp_path / "book.epub", basic_xhtml())

    with pytest.raises(EpubValidationError, match="still has"):
        repair_epub(source)

    assert not (tmp_path / "book-fixed.epub").exists()
    assert list(tmp_path.glob(".book-fixed.epub.*.tmp.epub")) == []


def test_write_failure_leaves_no_output_or_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    source = write_epub(tmp_path / "book.epub", basic_xhtml())

    def fail_write(*_args, **_kwargs):
        raise EpubWriteError("simulated failure")

    monkeypatch.setattr("pdf2epub.repair._write_archive", fail_write)
    with pytest.raises(EpubWriteError, match="simulated"):
        repair_epub(source)

    assert not (tmp_path / "book-fixed.epub").exists()
    assert list(tmp_path.glob(".book-fixed.epub.*.tmp.epub")) == []


def test_malformed_archive_has_focused_error(tmp_path: Path) -> None:
    source = tmp_path / "broken.epub"
    source.write_bytes(b"not a zip")

    with pytest.raises(EpubReadError, match="Unable to read EPUB archive"):
        repair_epub(source)


def test_missing_container_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "missing-container.epub"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "mimetype",
            b"application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )

    with pytest.raises(EpubReadError, match="container.xml"):
        repair_epub(source)


def test_malformed_package_xml_is_rejected(tmp_path: Path) -> None:
    source = write_epub(
        tmp_path / "malformed-package.epub",
        basic_xhtml(),
        package_override=b"<package>",
    )

    with pytest.raises(EpubReadError, match="package.opf"):
        repair_epub(source)


def test_malformed_xhtml_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_epubcheck(monkeypatch)
    source = write_epub(tmp_path / "malformed-content.epub", b"<html>")

    with pytest.raises(EpubReadError, match="book.xhtml"):
        repair_epub(source)

    assert not (tmp_path / "malformed-content-fixed.epub").exists()


def test_duplicate_entries_are_rejected(tmp_path: Path) -> None:
    source = write_epub(tmp_path / "duplicate.epub", basic_xhtml())
    with pytest.warns(UserWarning, match="Duplicate name"), zipfile.ZipFile(source, "a") as archive:
        archive.writestr("OPS/book.xhtml", basic_xhtml("<p>Duplicate.</p>"))

    with pytest.raises(EpubReadError, match="duplicate"):
        repair_epub(source)


def test_encrypted_entry_flag_is_rejected(tmp_path: Path) -> None:
    source = write_epub(tmp_path / "encrypted.epub", basic_xhtml())
    data = bytearray(source.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = 0
        while True:
            position = data.find(signature, position)
            if position < 0:
                break
            offset = position + flag_offset
            flags = int.from_bytes(data[offset : offset + 2], "little") | 0x1
            data[offset : offset + 2] = flags.to_bytes(2, "little")
            position += len(signature)
    source.write_bytes(data)

    with pytest.raises(EpubReadError, match="Encrypted"):
        repair_epub(source)


def test_unsafe_archive_entry_name_is_rejected(tmp_path: Path) -> None:
    source = write_epub(tmp_path / "unsafe.epub", basic_xhtml())
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("../outside.txt", b"unsafe")

    with pytest.raises(EpubReadError, match="unsafe entry"):
        repair_epub(source)


def test_archive_mimetype_layout_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = ValidationSummary(0, 0, 0, ())
    _allow_epubcheck(monkeypatch, before=clean, after=clean)
    source = write_epub(tmp_path / "layout.epub", basic_xhtml())
    with zipfile.ZipFile(source) as archive:
        entries = [
            (info.filename, archive.read(info))
            for info in archive.infolist()
            if info.filename != "mimetype"
        ]
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("OPS/", b"")
        archive.writestr("mimetype", b"application/epub+zip")
        for name, content in entries:
            archive.writestr(name, content)

    result = repair_epub(source)

    assert result.fixes == ("Normalized the position and compression of the EPUB mimetype entry.",)
    with zipfile.ZipFile(result.output_path) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED


def test_epubcheck_discovery_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pdf2epub.repair import _resolve_epubcheck_command

    explicit = tmp_path / "explicit.jar"
    environment = tmp_path / "environment.jar"
    explicit.write_bytes(b"jar")
    environment.write_bytes(b"jar")
    monkeypatch.setenv("EPUBCHECK_JAR", str(environment))
    monkeypatch.setattr("pdf2epub.repair._resolve_java", lambda: "/java")
    monkeypatch.setattr("pdf2epub.repair.shutil.which", lambda _name: "/epubcheck")

    command = _resolve_epubcheck_command(RepairOptions(epubcheck_jar=explicit))

    assert command == ("/java", "-jar", str(explicit))
    assert _resolve_epubcheck_command(RepairOptions()) == (
        "/java",
        "-jar",
        str(environment),
    )


def test_epubcheck_executable_fallback_and_missing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pdf2epub.repair import _resolve_epubcheck_command

    monkeypatch.delenv("EPUBCHECK_JAR", raising=False)
    monkeypatch.setattr("pdf2epub.repair.shutil.which", lambda _name: "/epubcheck")
    assert _resolve_epubcheck_command(RepairOptions()) == ("/epubcheck",)

    monkeypatch.setattr("pdf2epub.repair.shutil.which", lambda _name: None)
    with pytest.raises(EpubValidationError, match="required"):
        _resolve_epubcheck_command(RepairOptions())


def test_validation_counts_include_suppressed_locations() -> None:
    from pdf2epub.repair import _validation_summary

    summary = _validation_summary(
        {
            "messages": [
                {
                    "ID": "RSC-005",
                    "severity": "ERROR",
                    "message": "Missing alt",
                    "locations": [{}, {}],
                    "additionalLocations": 77,
                }
            ]
        }
    )

    assert summary.error_count == 79
