"""Small deterministic PDF fixtures built with the runtime pypdf dependency."""

from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    TextStringObject,
)

PdfLine = Tuple[str, float, float, float, str]


def write_pdf(
    path: Path,
    pages: Sequence[Sequence[PdfLine]],
    *,
    metadata: Optional[Dict[str, str]] = None,
    language: Optional[str] = None,
    outline_title: Optional[str] = None,
    encrypted: bool = False,
) -> Path:
    writer = PdfWriter()
    fonts = {
        "regular": _font(writer, "/Helvetica"),
        "bold": _font(writer, "/Helvetica-Bold"),
        "italic": _font(writer, "/Helvetica-Oblique"),
    }

    for page_lines in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): fonts["regular"],
                        NameObject("/F2"): fonts["bold"],
                        NameObject("/F3"): fonts["italic"],
                    }
                )
            }
        )
        commands = []
        for text, size, x, y, style in page_lines:
            font_key = {"regular": "F1", "bold": "F2", "italic": "F3"}[style]
            commands.append(
                "BT /{} {} Tf {} {} Td ({}) Tj ET".format(
                    font_key,
                    size,
                    x,
                    y,
                    _escape_pdf_text(text),
                )
            )
        content = DecodedStreamObject()
        content.set_data("\n".join(commands).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(content)

    if metadata:
        writer.add_metadata(metadata)
    if language:
        writer.root_object[NameObject("/Lang")] = TextStringObject(language)
    if outline_title and pages:
        writer.add_outline_item(outline_title, 0)
    if encrypted:
        writer.encrypt("secret")
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def regular_lines(*values: str) -> Sequence[PdfLine]:
    return [
        (value, 12.0, 72.0, 720.0 - index * 22.0, "regular") for index, value in enumerate(values)
    ]


def _font(writer: PdfWriter, base_font: str):
    dictionary = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject(base_font),
        }
    )
    return writer._add_object(dictionary)


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
