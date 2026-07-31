import os
import subprocess
import sys
from base64 import b64decode
from pathlib import Path

import pytest

from pdf2epub import ConversionOptions, RepairOptions, convert_pdf, repair_epub
from pdf2epub.repair import _resolve_java

from .epub_factory import basic_xhtml, write_epub
from .pdf_factory import write_pdf

_PNG_1X1 = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def test_representative_epub_passes_epubcheck(tmp_path: Path) -> None:
    jar = os.environ.get("EPUBCHECK_JAR")
    if not jar:
        pytest.skip("EPUBCHECK_JAR is not configured")
    pdf = write_pdf(
        tmp_path / "conformance.pdf",
        [
            [
                ("CHAPTER ONE", 24, 180, 720, "bold"),
                ("An EPUBCheck Fixture", 14, 190, 680, "italic"),
                ("This is a representative paragraph.", 12, 72, 630, "regular"),
            ],
            [
                ("CHAPTER TWO", 24, 180, 720, "bold"),
                ("The second chapter has more text.", 12, 72, 660, "regular"),
            ],
        ],
        metadata={"/Title": "Conformance & Escaping", "/Author": "Example Author"},
    )
    result = convert_pdf(
        pdf,
        options=ConversionOptions(language="en"),
    )

    completed = subprocess.run(
        [_resolve_java(), "-jar", jar, str(result.output_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_repaired_epub_passes_epubcheck(tmp_path: Path) -> None:
    jar = os.environ.get("EPUBCHECK_JAR")
    if not jar:
        pytest.skip("EPUBCHECK_JAR is not configured")
    source = write_epub(
        tmp_path / "repair.epub",
        basic_xhtml(
            '<span><p>Paragraph with <img src="cover.png"/>.</p></span>'
            "<span><tr><td>One</td></tr><tr><td>Two</td></tr></span>"
        ),
        empty_guide=True,
        extra_entries={"OPS/cover.png": _PNG_1X1},
        extra_manifest={"cover.png": "image/png"},
    )

    result = repair_epub(source)
    completed = subprocess.run(
        [_resolve_java(), "-jar", jar, str(result.output_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert result.before_error_count > 0
    assert result.after_error_count == 0


def test_full_rebuilt_epub_passes_epubcheck(tmp_path: Path) -> None:
    jar = os.environ.get("EPUBCHECK_JAR")
    if not jar:
        pytest.skip("EPUBCHECK_JAR is not configured")
    source = write_epub(
        tmp_path / "rebuild.epub",
        basic_xhtml(
            '<div class="paragraph">Артём Звёздин</div>'
            '<div class="paragraph"><strong>Биржа. Легко не будет.</strong></div>'
            '<div class="paragraph"><strong>Введение</strong></div>'
            '<div class="paragraph">Прак-тически полезный текст.</div>'
            '<div class="paragraph"><strong>Глава 1. Пример</strong></div>'
            '<div class="paragraph">Описание рисунка.</div>'
            '<div class="paragraph"><img src="chart.png"/></div>'
            '<div class="paragraph"><strong>Заключение</strong></div>'
            '<div class="paragraph">Итог.</div>'
        ),
        empty_guide=True,
        extra_entries={"OPS/chart.png": _PNG_1X1},
        extra_manifest={"chart.png": "image/png"},
    )

    result = repair_epub(
        source,
        options=RepairOptions(full_repair=True),
    )
    completed = subprocess.run(
        [_resolve_java(), "-jar", jar, str(result.output_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert result.epub_version == "3.3"
    assert result.chapter_count == 1
    assert result.after_error_count == 0


def test_console_and_legacy_cli_repair_epub(tmp_path: Path) -> None:
    jar = os.environ.get("EPUBCHECK_JAR")
    if not jar:
        pytest.skip("EPUBCHECK_JAR is not configured")
    root = Path(__file__).resolve().parents[1]
    source = write_epub(
        tmp_path / "cli.epub",
        basic_xhtml('<p><img src="cover.png"/></p>'),
        empty_guide=True,
        extra_entries={"OPS/cover.png": _PNG_1X1},
        extra_manifest={"cover.png": "image/png"},
    )
    console = Path(sys.executable).with_name("pdf2epub")

    installed = subprocess.run(
        [str(console), str(source), "--epubcheck-jar", jar],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    legacy = subprocess.run(
        [
            sys.executable,
            str(root / "convert_pdf.py"),
            str(source),
            "--epubcheck-jar",
            jar,
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert legacy.returncode == 0, legacy.stdout + legacy.stderr
    assert (tmp_path / "cli-fixed.epub").exists()
    assert (tmp_path / "cli-fixed-1.epub").exists()
