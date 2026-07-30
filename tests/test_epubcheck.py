import os
import subprocess
from pathlib import Path

import pytest

from pdf2epub import ConversionOptions, convert_pdf

from .pdf_factory import write_pdf


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
        ["java", "-jar", jar, str(result.output_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
