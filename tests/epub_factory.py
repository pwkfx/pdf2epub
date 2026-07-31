"""Small deterministic EPUB fixtures for repair tests."""

import zipfile
from pathlib import Path
from typing import Mapping, Optional


def write_epub(
    path: Path,
    xhtml: bytes,
    *,
    version: str = "2.0",
    empty_guide: bool = False,
    extra_entries: Optional[Mapping[str, bytes]] = None,
    extra_manifest: Optional[Mapping[str, str]] = None,
    package_override: Optional[bytes] = None,
) -> Path:
    package = package_override or _package(version, empty_guide, extra_manifest or {})
    entries = {
        "META-INF/container.xml": _container(),
        "OPS/package.opf": package,
        "OPS/book.xhtml": xhtml,
        "OPS/style.css": b"table.grid { border-collapse: collapse; }",
        "OPS/toc.ncx": _ncx(),
    }
    entries.update(extra_entries or {})
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "mimetype",
            b"application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def basic_xhtml(body: str = "<p>Text.</p>") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        "<head><title>Fixture</title></head>"
        "<body>{}</body></html>".format(body)
    ).encode("utf-8")


def _container() -> bytes:
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
        b'version="1.0"><rootfiles><rootfile full-path="OPS/package.opf" '
        b'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )


def _package(
    version: str,
    empty_guide: bool,
    extra_manifest: Mapping[str, str],
) -> bytes:
    guide = "<guide/>" if empty_guide else ""
    additional_items = "".join(
        '<item id="extra-{}" href="{}" media-type="{}"/>'.format(
            index,
            href,
            media_type,
        )
        for index, (href, media_type) in enumerate(extra_manifest.items(), start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="{}" '
        'unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Original Title</dc:title><dc:creator>Original Author</dc:creator>"
        '<dc:language>en</dc:language><dc:identifier id="bookid">fixture-id</dc:identifier>'
        "</metadata><manifest>"
        '<item id="book" href="book.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="css" href="style.css" media-type="text/css"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        "{}"
        '</manifest><spine toc="ncx"><itemref idref="book"/></spine>{}</package>'.format(
            version,
            additional_items,
            guide,
        )
    ).encode("utf-8")


def _ncx() -> bytes:
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        b'<head><meta name="dtb:uid" content="fixture-id"/>'
        b'<meta name="dtb:depth" content="1"/>'
        b'<meta name="dtb:totalPageCount" content="0"/>'
        b'<meta name="dtb:maxPageNumber" content="0"/></head>'
        b"<docTitle><text>Original Title</text></docTitle><navMap>"
        b'<navPoint id="book" playOrder="1"><navLabel><text>Book</text></navLabel>'
        b'<content src="book.xhtml"/></navPoint></navMap></ncx>'
    )
