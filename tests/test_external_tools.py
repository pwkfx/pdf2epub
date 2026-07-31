import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from pdf2epub import ConversionOptions, convert_document

from .docx_factory import write_docx

_DJVU_TOOLS = ("c44", "ddjvu", "djvused", "djvutxt", "tesseract")
pytestmark = pytest.mark.skipif(
    os.environ.get("PDF2EPUB_RUN_EXTERNAL_TESTS") != "1",
    reason="external conversion tests require PDF2EPUB_RUN_EXTERNAL_TESTS=1",
)


def _large_font() -> object:
    font_paths = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    )
    font_path = next((path for path in font_paths if path.is_file()), None)
    return ImageFont.truetype(str(font_path), 64) if font_path is not None else None


@pytest.mark.external_tools
@pytest.mark.skipif(
    not any(shutil.which(name) for name in ("soffice", "libreoffice")),
    reason="LibreOffice is not installed",
)
def test_legacy_doc_external_conversion(tmp_path: Path) -> None:
    docx = write_docx(tmp_path / "legacy.docx")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    profile = tmp_path / "producer-profile"
    cache = tmp_path / "producer-cache"
    profile.mkdir()
    cache.mkdir()
    environment = os.environ.copy()
    environment["XDG_CACHE_HOME"] = str(cache)
    completed = subprocess.run(
        [
            str(soffice),
            "-env:UserInstallation={}".format(profile.resolve().as_uri()),
            "--headless",
            "--convert-to",
            'doc:"MS Word 97"',
            "--outdir",
            str(tmp_path),
            str(docx),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        env=environment,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    legacy = tmp_path / "legacy.doc"
    assert legacy.is_file()

    result = convert_document(legacy)

    assert result.source_format == "doc"
    assert result.title == "DOCX Title"
    assert result.output_path.is_file()


@pytest.mark.external_tools
@pytest.mark.skipif(
    not all(shutil.which(name) for name in _DJVU_TOOLS),
    reason="DjVuLibre and Tesseract tools are not installed",
)
def test_image_only_djvu_external_ocr_conversion(tmp_path: Path) -> None:
    image = Image.new("RGB", (1000, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((50, 90), "RECOGNIZED DJVU TEXT", fill="black", font=_large_font())
    source_image = tmp_path / "page.ppm"
    image.save(source_image, format="PPM")
    djvu = tmp_path / "scan.djvu"
    completed = subprocess.run(
        [str(shutil.which("c44")), str(source_image), str(djvu)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    result = convert_document(
        djvu,
        options=ConversionOptions(language="en", ocr_language="eng"),
    )

    assert result.source_format == "djvu"
    assert result.ocr_page_count == 1
    assert result.image_count == 1
    assert result.output_path.is_file()


@pytest.mark.external_tools
@pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="Tesseract is not installed",
)
def test_image_only_pdf_external_ocr_conversion(tmp_path: Path) -> None:
    image = Image.new("RGB", (1200, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((50, 140), "RECOGNIZED PDF TEXT", fill="black", font=_large_font())
    pdf = tmp_path / "scan.pdf"
    image.save(pdf, format="PDF", resolution=150)

    result = convert_document(
        pdf,
        options=ConversionOptions(language="en", ocr_language="eng"),
    )

    assert result.source_format == "pdf"
    assert result.ocr_page_count == 1
    assert result.output_path.is_file()
