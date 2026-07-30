import subprocess
import sys
from pathlib import Path

from pdf2epub.cli import main

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
