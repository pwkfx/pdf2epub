import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf2epub.cli import main
from pdf2epub.repair import ValidationSummary

from .epub_factory import basic_xhtml, write_epub
from .pdf_factory import regular_lines, write_pdf


def test_cli_main_returns_zero_and_prints_verbose_metadata(
    tmp_path: Path,
    capsys,
) -> None:
    pdf = write_pdf(
        tmp_path / "book.pdf",
        [regular_lines("Ordinary text.")],
        metadata={"/Title": "CLI Book"},
    )

    status = main([str(pdf), "--language", "en", "--verbose"])
    captured = capsys.readouterr()

    assert status == 0
    assert "Title: CLI Book" in captured.out
    assert "Language: en" in captured.out
    assert "EPUB generated at:" in captured.out


def test_cli_conversion_error_returns_one(tmp_path: Path, capsys) -> None:
    status = main([str(tmp_path / "missing.pdf")])
    captured = capsys.readouterr()

    assert status == 1
    assert "Error:" in captured.err


def test_cli_repairs_epub_and_prints_verbose_summary(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    epub = write_epub(
        tmp_path / "book.epub",
        basic_xhtml('<p><img src="cover.jpg"/></p>'),
        empty_guide=True,
    )
    summaries = iter(
        [
            ValidationSummary(0, 2, 0, ()),
            ValidationSummary(0, 0, 0, ()),
        ]
    )
    monkeypatch.setattr(
        "pdf2epub.repair._resolve_epubcheck_command",
        lambda _options: ("fake-epubcheck",),
    )
    monkeypatch.setattr(
        "pdf2epub.repair._run_epubcheck",
        lambda _command, _publication: next(summaries),
    )

    status = main([str(epub), "--verbose"])
    captured = capsys.readouterr()

    assert status == 0
    assert "EPUB version: 2.0" in captured.out
    assert "EPUBCheck before: 0 fatal, 2 errors, 0 warnings" in captured.out
    assert "Added empty alt attributes to 1 images." in captured.out
    assert "EPUB repaired at:" in captured.out


def test_cli_rejects_pdf_metadata_options_for_epub(tmp_path: Path, capsys) -> None:
    epub = write_epub(tmp_path / "book.epub", basic_xhtml())

    status = main([str(epub), "--title", "Changed"])
    captured = capsys.readouterr()

    assert status == 1
    assert "apply only to PDF" in captured.err


def test_cli_rejects_full_repair_for_pdf(tmp_path: Path, capsys) -> None:
    pdf = write_pdf(tmp_path / "book.pdf", [regular_lines("Text.")])

    status = main([str(pdf), "--full-repair"])
    captured = capsys.readouterr()

    assert status == 1
    assert "applies only to EPUB" in captured.err


def test_cli_passes_full_repair_option_to_epub_pipeline(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received = {}

    def rebuild(_input, _output, *, options):
        received["options"] = options
        return SimpleNamespace(
            output_path=tmp_path / "book-rebuilt.epub",
            epub_version="3.3",
            title="Recovered",
            author="Author",
            language="ru",
            chapter_count=2,
            before_fatal_count=0,
            before_error_count=3,
            before_warning_count=0,
            after_fatal_count=0,
            after_error_count=0,
            after_warning_count=0,
            fixes=("Rebuilt.",),
            warnings=(),
        )

    monkeypatch.setattr("pdf2epub.cli.repair_epub", rebuild)

    status = main(
        [
            str(tmp_path / "book.epub"),
            "--full-repair",
            "--epubcheck-jar",
            "/tmp/epubcheck.jar",
            "--verbose",
        ]
    )
    captured = capsys.readouterr()

    assert status == 0
    assert received["options"].full_repair is True
    assert str(received["options"].epubcheck_jar) == "/tmp/epubcheck.jar"
    assert "Title: Recovered" in captured.out
    assert "Chapters: 2" in captured.out


def test_cli_rejects_unknown_input_extension(tmp_path: Path, capsys) -> None:
    source = tmp_path / "book.txt"
    source.write_text("text")

    status = main([str(source)])
    captured = capsys.readouterr()

    assert status == 1
    assert ".pdf or .epub" in captured.err


def test_legacy_script_wrapper_converts_from_source_checkout(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    pdf = write_pdf(tmp_path / "legacy.pdf", [regular_lines("Legacy invocation.")])

    completed = subprocess.run(
        [sys.executable, str(root / "convert_pdf.py"), str(pdf), "--language", "en"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "legacy.epub").exists()
    assert "EPUB generated at:" in completed.stdout


def test_module_entry_point_reports_argument_errors() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = {"PYTHONPATH": str(root / "src")}

    completed = subprocess.run(
        [sys.executable, "-m", "pdf2epub"],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "usage:" in completed.stderr
