"""EPUB 3.3 resource rendering, validation, and atomic archive writing."""

import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Sequence
from xml.etree import ElementTree as ET

from .errors import EpubWriteError
from .models import PublicationMetadata, Section

_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_EPUB_NS = "http://www.idpf.org/2007/ops"
_NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
_OPF_NS = "http://www.idpf.org/2007/opf"
_XHTML_NS = "http://www.w3.org/1999/xhtml"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_NCX_DOCTYPE = (
    '<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" '
    '"http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">'
)

_CSS = """body {
  font-family: serif;
  padding: 2%;
}
h1 {
  text-align: center;
  margin-top: 2em;
  margin-bottom: 1em;
  font-size: 1.4em;
  break-before: page;
  page-break-before: always;
}
p.subtitle {
  text-align: center;
  margin: 1em 0;
  font-weight: normal;
  font-style: italic;
  text-indent: 0;
}
p {
  text-align: justify;
  text-indent: 1.5em;
  margin: 0.3em 0;
  line-height: 1.4;
}
"""


def write_epub(
    output_path: Path,
    metadata: PublicationMetadata,
    sections: Sequence[Section],
    *,
    overwrite: bool,
) -> None:
    """Render, validate, and atomically write an EPUB archive."""

    if not sections:
        raise EpubWriteError("Cannot create an EPUB without content sections.")
    resources = _build_resources(metadata, sections)
    _validate_resources(resources)

    output_path = output_path.resolve(strict=False)
    if output_path.exists() and not overwrite:
        raise EpubWriteError("Output file already exists: {}".format(output_path))

    temporary_path = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=str(output_path.parent),
            prefix=".{}.".format(output_path.name),
            suffix=".tmp",
        )
        os.close(file_descriptor)
        temporary_path = Path(temporary_name)
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            archive.writestr(
                "mimetype",
                resources["mimetype"],
                compress_type=zipfile.ZIP_STORED,
            )
            for name, content in resources.items():
                if name != "mimetype":
                    archive.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
        if output_path.exists() and not overwrite:
            raise EpubWriteError("Output file already exists: {}".format(output_path))
        os.replace(str(temporary_path), str(output_path))
        temporary_path = None
    except EpubWriteError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise EpubWriteError("Unable to write EPUB '{}': {}".format(output_path, exc)) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _build_resources(
    metadata: PublicationMetadata,
    sections: Sequence[Section],
) -> Dict[str, bytes]:
    resources: Dict[str, bytes] = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": _container_xml(),
        "EPUB/styles/book.css": _CSS.encode("utf-8"),
    }
    for section in sections:
        resources["EPUB/{}".format(section.filename)] = _section_xhtml(metadata, section)
    resources["EPUB/nav.xhtml"] = _navigation_xhtml(metadata, sections)
    resources["EPUB/toc.ncx"] = _ncx(metadata, sections)
    resources["EPUB/package.opf"] = _package_document(metadata, sections)
    return resources


def _container_xml() -> bytes:
    ET.register_namespace("", _CONTAINER_NS)
    root = ET.Element(
        _qualified(_CONTAINER_NS, "container"),
        {"version": "1.0"},
    )
    rootfiles = ET.SubElement(root, _qualified(_CONTAINER_NS, "rootfiles"))
    ET.SubElement(
        rootfiles,
        _qualified(_CONTAINER_NS, "rootfile"),
        {
            "full-path": "EPUB/package.opf",
            "media-type": "application/oebps-package+xml",
        },
    )
    return _serialize(root)


def _section_xhtml(metadata: PublicationMetadata, section: Section) -> bytes:
    ET.register_namespace("", _XHTML_NS)
    ET.register_namespace("epub", _EPUB_NS)
    root = ET.Element(
        _qualified(_XHTML_NS, "html"),
        {
            "lang": metadata.language,
            _qualified(_XML_NS, "lang"): metadata.language,
        },
    )
    head = ET.SubElement(root, _qualified(_XHTML_NS, "head"))
    ET.SubElement(head, _qualified(_XHTML_NS, "title")).text = _xml_text(section.title)
    ET.SubElement(
        head,
        _qualified(_XHTML_NS, "link"),
        {
            "rel": "stylesheet",
            "type": "text/css",
            "href": "../styles/book.css",
        },
    )
    body = ET.SubElement(root, _qualified(_XHTML_NS, "body"))
    heading_written = False
    for block in section.blocks:
        if block.kind == "heading":
            attributes = {}
            if not heading_written:
                attributes["id"] = section.navigation_id
                heading_written = True
            element = ET.SubElement(body, _qualified(_XHTML_NS, "h1"), attributes)
        elif block.kind == "subtitle":
            element = ET.SubElement(
                body,
                _qualified(_XHTML_NS, "p"),
                {"class": "subtitle"},
            )
        else:
            element = ET.SubElement(body, _qualified(_XHTML_NS, "p"))
        element.text = _xml_text(block.text)
    return _serialize(root)


def _navigation_xhtml(
    metadata: PublicationMetadata,
    sections: Sequence[Section],
) -> bytes:
    ET.register_namespace("", _XHTML_NS)
    ET.register_namespace("epub", _EPUB_NS)
    root = ET.Element(
        _qualified(_XHTML_NS, "html"),
        {
            "lang": metadata.language,
            _qualified(_XML_NS, "lang"): metadata.language,
        },
    )
    head = ET.SubElement(root, _qualified(_XHTML_NS, "head"))
    ET.SubElement(head, _qualified(_XHTML_NS, "title")).text = "Contents"
    ET.SubElement(
        head,
        _qualified(_XHTML_NS, "link"),
        {"rel": "stylesheet", "type": "text/css", "href": "styles/book.css"},
    )
    body = ET.SubElement(root, _qualified(_XHTML_NS, "body"))
    navigation = ET.SubElement(
        body,
        _qualified(_XHTML_NS, "nav"),
        {_qualified(_EPUB_NS, "type"): "toc", "id": "toc"},
    )
    ET.SubElement(navigation, _qualified(_XHTML_NS, "h1")).text = "Contents"
    ordered_list = ET.SubElement(navigation, _qualified(_XHTML_NS, "ol"))
    for section in sections:
        item = ET.SubElement(ordered_list, _qualified(_XHTML_NS, "li"))
        target = section.filename
        if any(block.kind == "heading" for block in section.blocks):
            target = "{}#{}".format(target, section.navigation_id)
        link = ET.SubElement(
            item,
            _qualified(_XHTML_NS, "a"),
            {"href": target},
        )
        link.text = _xml_text(section.title)
    return _serialize(root)


def _ncx(metadata: PublicationMetadata, sections: Sequence[Section]) -> bytes:
    ET.register_namespace("", _NCX_NS)
    root = ET.Element(_qualified(_NCX_NS, "ncx"), {"version": "2005-1"})
    head = ET.SubElement(root, _qualified(_NCX_NS, "head"))
    ET.SubElement(
        head,
        _qualified(_NCX_NS, "meta"),
        {"name": "dtb:uid", "content": metadata.identifier},
    )
    ET.SubElement(
        head,
        _qualified(_NCX_NS, "meta"),
        {"name": "dtb:depth", "content": "1"},
    )
    ET.SubElement(
        head,
        _qualified(_NCX_NS, "meta"),
        {"name": "dtb:totalPageCount", "content": "0"},
    )
    ET.SubElement(
        head,
        _qualified(_NCX_NS, "meta"),
        {"name": "dtb:maxPageNumber", "content": "0"},
    )
    doc_title = ET.SubElement(root, _qualified(_NCX_NS, "docTitle"))
    ET.SubElement(doc_title, _qualified(_NCX_NS, "text")).text = _xml_text(metadata.title)
    if metadata.author:
        doc_author = ET.SubElement(root, _qualified(_NCX_NS, "docAuthor"))
        ET.SubElement(doc_author, _qualified(_NCX_NS, "text")).text = _xml_text(metadata.author)
    navigation_map = ET.SubElement(root, _qualified(_NCX_NS, "navMap"))
    for index, section in enumerate(sections, 1):
        point = ET.SubElement(
            navigation_map,
            _qualified(_NCX_NS, "navPoint"),
            {"id": "navPoint-{}".format(index), "playOrder": str(index)},
        )
        label = ET.SubElement(point, _qualified(_NCX_NS, "navLabel"))
        ET.SubElement(label, _qualified(_NCX_NS, "text")).text = _xml_text(section.title)
        target = section.filename
        if any(block.kind == "heading" for block in section.blocks):
            target = "{}#{}".format(target, section.navigation_id)
        ET.SubElement(
            point,
            _qualified(_NCX_NS, "content"),
            {"src": target},
        )
    return _serialize(root, doctype=_NCX_DOCTYPE)


def _package_document(
    metadata: PublicationMetadata,
    sections: Sequence[Section],
) -> bytes:
    ET.register_namespace("", _OPF_NS)
    ET.register_namespace("dc", _DC_NS)
    root = ET.Element(
        _qualified(_OPF_NS, "package"),
        {
            "version": "3.0",
            "unique-identifier": "pub-id",
            _qualified(_XML_NS, "lang"): metadata.language,
        },
    )
    metadata_element = ET.SubElement(root, _qualified(_OPF_NS, "metadata"))
    ET.SubElement(
        metadata_element,
        _qualified(_DC_NS, "identifier"),
        {"id": "pub-id"},
    ).text = _xml_text(metadata.identifier)
    ET.SubElement(metadata_element, _qualified(_DC_NS, "title")).text = _xml_text(metadata.title)
    ET.SubElement(metadata_element, _qualified(_DC_NS, "language")).text = _xml_text(
        metadata.language
    )
    if metadata.author:
        ET.SubElement(metadata_element, _qualified(_DC_NS, "creator")).text = _xml_text(
            metadata.author
        )
    ET.SubElement(
        metadata_element,
        _qualified(_OPF_NS, "meta"),
        {"property": "dcterms:modified"},
    ).text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = ET.SubElement(root, _qualified(_OPF_NS, "manifest"))
    ET.SubElement(
        manifest,
        _qualified(_OPF_NS, "item"),
        {
            "id": "nav",
            "href": "nav.xhtml",
            "media-type": "application/xhtml+xml",
            "properties": "nav",
        },
    )
    ET.SubElement(
        manifest,
        _qualified(_OPF_NS, "item"),
        {
            "id": "ncx",
            "href": "toc.ncx",
            "media-type": "application/x-dtbncx+xml",
        },
    )
    ET.SubElement(
        manifest,
        _qualified(_OPF_NS, "item"),
        {
            "id": "css",
            "href": "styles/book.css",
            "media-type": "text/css",
        },
    )
    for index, section in enumerate(sections, 1):
        ET.SubElement(
            manifest,
            _qualified(_OPF_NS, "item"),
            {
                "id": "section-{}".format(index),
                "href": section.filename,
                "media-type": "application/xhtml+xml",
            },
        )

    spine = ET.SubElement(root, _qualified(_OPF_NS, "spine"), {"toc": "ncx"})
    for index, _section in enumerate(sections, 1):
        ET.SubElement(
            spine,
            _qualified(_OPF_NS, "itemref"),
            {"idref": "section-{}".format(index)},
        )
    return _serialize(root)


def _validate_resources(resources: Dict[str, bytes]) -> None:
    try:
        package = ET.fromstring(resources["EPUB/package.opf"])
        manifest_items = package.findall(".//{{{}}}manifest/{{{}}}item".format(_OPF_NS, _OPF_NS))
        item_ids = {item.attrib["id"]: item.attrib["href"] for item in manifest_items}
        for href in item_ids.values():
            resource_name = "EPUB/{}".format(href)
            if resource_name not in resources:
                raise EpubWriteError(
                    "Package manifest references a missing resource: {}".format(href)
                )
        for itemref in package.findall(".//{{{}}}spine/{{{}}}itemref".format(_OPF_NS, _OPF_NS)):
            if itemref.attrib.get("idref") not in item_ids:
                raise EpubWriteError(
                    "Package spine references an unknown manifest item: {}".format(
                        itemref.attrib.get("idref")
                    )
                )
        _validate_navigation_targets(resources["EPUB/nav.xhtml"], resources, "href")
        _validate_navigation_targets(resources["EPUB/toc.ncx"], resources, "src")
    except EpubWriteError:
        raise
    except (ET.ParseError, KeyError, TypeError, ValueError) as exc:
        raise EpubWriteError("Generated EPUB resources are invalid: {}".format(exc)) from exc


def _validate_navigation_targets(
    navigation_content: bytes,
    resources: Dict[str, bytes],
    attribute: str,
) -> None:
    root = ET.fromstring(navigation_content)
    targets = [element.attrib[attribute] for element in root.iter() if attribute in element.attrib]
    for target in targets:
        filename, separator, anchor = target.partition("#")
        resource_name = "EPUB/{}".format(filename)
        if resource_name not in resources:
            raise EpubWriteError("Navigation references a missing resource: {}".format(target))
        if separator:
            content = ET.fromstring(resources[resource_name])
            if not any(element.attrib.get("id") == anchor for element in content.iter()):
                raise EpubWriteError("Navigation references a missing anchor: {}".format(target))


def _xml_text(value: str) -> str:
    return "".join(
        character for character in str(value) if character in "\t\n\r" or ord(character) >= 0x20
    )


def _qualified(namespace: str, name: str) -> str:
    return "{{{}}}{}".format(namespace, name)


def _serialize(element: ET.Element, doctype: str = "") -> bytes:
    content = ET.tostring(
        element,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )
    if doctype:
        declaration, body = content.split(b"?>", 1)
        return declaration + b"?>\n" + doctype.encode("ascii") + body
    return content
