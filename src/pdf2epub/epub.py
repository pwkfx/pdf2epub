"""EPUB 3.3 resource rendering, validation, and atomic archive writing."""

import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence
from xml.etree import ElementTree as ET

from .errors import EpubWriteError
from .models import (
    NavigationEntry,
    PageEntry,
    PreparedPublication,
    PublicationMetadata,
    Section,
)

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
img {
  max-width: 100%;
  height: auto;
}
figure {
  margin: 1em auto;
  text-align: center;
}
.front-matter-page {
  break-after: page;
  page-break-after: always;
}
.front-matter-page img {
  max-height: 94vh;
  object-fit: contain;
}
table {
  border-collapse: collapse;
  margin: 1em auto;
  max-width: 100%;
}
td, th {
  border: 1px solid #888;
  padding: 0.3em;
}
li > p {
  text-indent: 0;
}
body.fixed-page {
  margin: 0;
  padding: 0;
  overflow: hidden;
}
.facsimile {
  position: relative;
  margin: 0;
}
.facsimile-image, .text-layer {
  position: absolute;
  inset: 0;
}
.facsimile-image {
  width: 100%;
  height: 100%;
}
.text-layer span {
  position: absolute;
  color: transparent;
  white-space: pre;
  line-height: 1;
  transform-origin: left top;
  user-select: text;
}
.text-layer span::selection {
  background: rgba(60, 130, 255, 0.35);
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
    _write_resources(output_path, resources, overwrite=overwrite)


def write_publication(
    output_path: Path,
    metadata: PublicationMetadata,
    publication: PreparedPublication,
    *,
    overwrite: bool,
) -> None:
    """Validate and atomically write a resource-rich EPUB publication."""

    if not publication.sections:
        raise EpubWriteError("Cannot create an EPUB without content sections.")
    resources = _build_publication_resources(metadata, publication)
    _validate_resources(resources)
    _validate_embedded_targets(resources)
    _write_resources(output_path, resources, overwrite=overwrite)


def _write_resources(
    output_path: Path,
    resources: Dict[str, bytes],
    *,
    overwrite: bool,
) -> None:
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


def _build_publication_resources(
    metadata: PublicationMetadata,
    publication: PreparedPublication,
) -> Dict[str, bytes]:
    resources: Dict[str, bytes] = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": _container_xml(),
        "EPUB/styles/book.css": _CSS.encode("utf-8"),
    }
    seen = set(resources)
    for section in publication.sections:
        name = "EPUB/{}".format(section.filename)
        if name in seen:
            raise EpubWriteError("Duplicate generated EPUB resource: {}".format(name))
        seen.add(name)
        resources[name] = section.content
    for resource in publication.resources:
        name = "EPUB/{}".format(resource.filename)
        if name in seen:
            raise EpubWriteError("Duplicate generated EPUB resource: {}".format(name))
        seen.add(name)
        resources[name] = resource.content
    resources["EPUB/nav.xhtml"] = _publication_navigation_xhtml(
        metadata,
        publication.navigation,
        publication.page_list,
    )
    resources["EPUB/toc.ncx"] = _publication_ncx(metadata, publication.navigation)
    resources["EPUB/package.opf"] = _publication_package_document(metadata, publication)
    return resources


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
        if block.kind in {"image", "front-image"} and block.resource_href:
            attributes = {"class": "front-matter-page"} if block.kind == "front-image" else {}
            figure = ET.SubElement(
                body,
                _qualified(_XHTML_NS, "figure"),
                attributes,
            )
            ET.SubElement(
                figure,
                _qualified(_XHTML_NS, "img"),
                {
                    "src": "../{}".format(block.resource_href),
                    "alt": _xml_text(block.alt),
                },
            )
            continue
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


def _publication_navigation_xhtml(
    metadata: PublicationMetadata,
    entries: Sequence[NavigationEntry],
    page_list: Sequence[PageEntry],
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
    _append_navigation_entries(ordered_list, entries)

    if page_list:
        pages = ET.SubElement(
            body,
            _qualified(_XHTML_NS, "nav"),
            {_qualified(_EPUB_NS, "type"): "page-list", "id": "page-list"},
        )
        ET.SubElement(pages, _qualified(_XHTML_NS, "h2")).text = "Pages"
        pages_list = ET.SubElement(pages, _qualified(_XHTML_NS, "ol"))
        for page in page_list:
            item = ET.SubElement(pages_list, _qualified(_XHTML_NS, "li"))
            ET.SubElement(
                item,
                _qualified(_XHTML_NS, "a"),
                {"href": page.href},
            ).text = _xml_text(page.label)
    return _serialize(root)


def _append_navigation_entries(
    parent: ET.Element,
    entries: Sequence[NavigationEntry],
) -> None:
    for entry in entries:
        item = ET.SubElement(parent, _qualified(_XHTML_NS, "li"))
        ET.SubElement(
            item,
            _qualified(_XHTML_NS, "a"),
            {"href": entry.href},
        ).text = _xml_text(entry.title)
        if entry.children:
            child_list = ET.SubElement(item, _qualified(_XHTML_NS, "ol"))
            _append_navigation_entries(child_list, entry.children)


def _publication_ncx(
    metadata: PublicationMetadata,
    entries: Sequence[NavigationEntry],
) -> bytes:
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
        {"name": "dtb:depth", "content": str(max(1, _navigation_depth(entries)))},
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
    play_order = [0]
    _append_ncx_entries(navigation_map, entries, play_order)
    return _serialize(root, doctype=_NCX_DOCTYPE)


def _append_ncx_entries(
    parent: ET.Element,
    entries: Sequence[NavigationEntry],
    play_order: List[int],
) -> None:
    for entry in entries:
        play_order[0] += 1
        point = ET.SubElement(
            parent,
            _qualified(_NCX_NS, "navPoint"),
            {
                "id": "navPoint-{}".format(play_order[0]),
                "playOrder": str(play_order[0]),
            },
        )
        label = ET.SubElement(point, _qualified(_NCX_NS, "navLabel"))
        ET.SubElement(label, _qualified(_NCX_NS, "text")).text = _xml_text(entry.title)
        ET.SubElement(
            point,
            _qualified(_NCX_NS, "content"),
            {"src": entry.href},
        )
        _append_ncx_entries(point, entry.children, play_order)


def _navigation_depth(entries: Sequence[NavigationEntry]) -> int:
    if not entries:
        return 0
    return max(1 + _navigation_depth(entry.children) for entry in entries)


def _publication_package_document(
    metadata: PublicationMetadata,
    publication: PreparedPublication,
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
    if publication.fixed_layout:
        for property_name, value in (
            ("rendition:layout", "pre-paginated"),
            ("rendition:orientation", "auto"),
            ("rendition:spread", "none"),
        ):
            ET.SubElement(
                metadata_element,
                _qualified(_OPF_NS, "meta"),
                {"property": property_name},
            ).text = value

    manifest = ET.SubElement(root, _qualified(_OPF_NS, "manifest"))
    for item_id, href, media_type, properties in (
        ("nav", "nav.xhtml", "application/xhtml+xml", "nav"),
        ("ncx", "toc.ncx", "application/x-dtbncx+xml", ""),
        ("css", "styles/book.css", "text/css", ""),
    ):
        attributes = {"id": item_id, "href": href, "media-type": media_type}
        if properties:
            attributes["properties"] = properties
        ET.SubElement(manifest, _qualified(_OPF_NS, "item"), attributes)
    for index, section in enumerate(publication.sections, 1):
        ET.SubElement(
            manifest,
            _qualified(_OPF_NS, "item"),
            {
                "id": "section-{}".format(index),
                "href": section.filename,
                "media-type": "application/xhtml+xml",
            },
        )
    for index, resource in enumerate(publication.resources, 1):
        attributes = {
            "id": "resource-{}".format(index),
            "href": resource.filename,
            "media-type": resource.media_type,
        }
        if resource.properties:
            attributes["properties"] = resource.properties
        ET.SubElement(manifest, _qualified(_OPF_NS, "item"), attributes)

    spine = ET.SubElement(root, _qualified(_OPF_NS, "spine"), {"toc": "ncx"})
    for index, _section in enumerate(publication.sections, 1):
        ET.SubElement(
            spine,
            _qualified(_OPF_NS, "itemref"),
            {("idref"): "section-{}".format(index)},
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


def _validate_embedded_targets(resources: Dict[str, bytes]) -> None:
    for name, content in resources.items():
        if not name.endswith((".xhtml", ".html", ".htm")):
            continue
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise EpubWriteError("Generated XHTML resource is invalid: {}".format(name)) from exc
        for element in root.iter():
            local_name = element.tag.rsplit("}", 1)[-1]
            attribute = "src" if local_name == "img" else None
            if local_name == "link" and element.attrib.get("rel") == "stylesheet":
                attribute = "href"
            if attribute is None or attribute not in element.attrib:
                continue
            target = element.attrib[attribute].split("#", 1)[0]
            if not target or "://" in target or target.startswith(("data:", "/")):
                continue
            import posixpath

            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if resolved not in resources:
                raise EpubWriteError(
                    "XHTML resource references a missing resource: {} from {}".format(target, name)
                )


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
        character
        for character in str(value)
        if character in "\t\n\r"
        or 0x20 <= ord(character) <= 0xD7FF
        or 0xE000 <= ord(character) <= 0xFFFD
        or 0x10000 <= ord(character) <= 0x10FFFF
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
