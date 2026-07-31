"""Semantic DOCX extraction and legacy DOC conversion."""

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from .errors import DocumentReadError, MissingDependencyError
from .models import (
    EpubResource,
    NavigationEntry,
    PreparedPublication,
    PublicationMetadata,
    RenderedSection,
)

_XHTML_NS = "http://www.w3.org/1999/xhtml"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_CORE_NAMESPACES = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
}
_ALLOWED_TAGS = {
    "a",
    "abbr",
    "blockquote",
    "br",
    "caption",
    "code",
    "dd",
    "del",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "section",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
_DROP_TAGS = {"applet", "audio", "embed", "iframe", "object", "script", "style", "video"}
_GLOBAL_ATTRIBUTES = {"class", "dir", "id", "lang", "title"}
_TAG_ATTRIBUTES = {
    "a": {"href"},
    "img": {"alt", "height", "src", "width"},
    "ol": {"start", "type"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}
_IMAGE_MEDIA = {
    "image/png": (".png", "image/png"),
    "image/jpeg": (".jpg", "image/jpeg"),
    "image/jpg": (".jpg", "image/jpeg"),
    "image/gif": (".gif", "image/gif"),
    "image/svg+xml": (".svg", "image/svg+xml"),
    "image/webp": (".webp", "image/webp"),
}


@dataclass(frozen=True)
class WordSource:
    """Sanitized semantic Word content held fully in memory."""

    elements: Tuple[bytes, ...]
    resources: Tuple[EpubResource, ...]
    title: Optional[str]
    author: Optional[str]
    language: Optional[str]
    warnings: Tuple[str, ...]


@dataclass
class _MutableNavigation:
    title: str
    href: str
    level: int
    children: List["_MutableNavigation"] = field(default_factory=list)


def extract_word_document(path: Path) -> WordSource:
    """Extract DOCX directly or convert a legacy DOC into temporary DOCX."""

    if path.suffix.casefold() == ".doc":
        return _extract_legacy_doc(path)
    return _extract_docx(path)


def build_word_publication(
    source: WordSource,
    metadata: PublicationMetadata,
) -> PreparedPublication:
    """Split semantic Word content into XHTML sections and nested navigation."""

    elements = [ET.fromstring(content) for content in source.elements]
    groups: List[Tuple[str, List[ET.Element], bool]] = []
    pending: List[ET.Element] = []
    chapter_number = 0
    for element in elements:
        if _local_name(element.tag) != "h1":
            pending.append(element)
            continue
        if pending:
            if chapter_number == 0:
                groups.append(("Preface", pending, True))
            else:
                groups.append((_heading_text(pending) or "Chapter", pending, False))
            pending = []
        chapter_number += 1
        pending.append(element)
    if pending:
        if chapter_number:
            groups.append((_heading_text(pending) or "Chapter", pending, False))
        else:
            groups.append((metadata.title, pending, False))
    if not groups:
        raise DocumentReadError("The Word document contains no convertible content.")

    sections: List[RenderedSection] = []
    navigation: List[NavigationEntry] = []
    chapter_index = 0
    for section_index, (title, nodes, is_preface) in enumerate(groups, 1):
        if is_preface:
            filename = "text/preface.xhtml"
        elif chapter_number:
            chapter_index += 1
            filename = "text/chapter-{:03d}.xhtml".format(chapter_index)
        else:
            filename = "text/content.xhtml"
        body_id = "section-{:03d}".format(section_index)
        section_navigation = _prepare_navigation(nodes, filename, body_id, title)
        navigation.extend(section_navigation)
        content = _word_xhtml(metadata.language, title, nodes, body_id)
        sections.append(RenderedSection(filename, title, content))

    return PreparedPublication(
        sections=tuple(sections),
        resources=source.resources,
        navigation=tuple(navigation),
        warnings=source.warnings,
    )


def _extract_legacy_doc(path: Path) -> WordSource:
    soffice = _find_soffice()
    with tempfile.TemporaryDirectory(prefix="pdf2epub-doc-") as temporary_name:
        temporary = Path(temporary_name)
        output_directory = temporary / "output"
        profile_directory = temporary / "profile"
        cache_directory = temporary / "cache"
        output_directory.mkdir()
        profile_directory.mkdir()
        cache_directory.mkdir()
        command = [
            soffice,
            "-env:UserInstallation={}".format(profile_directory.resolve().as_uri()),
            "--headless",
            "--convert-to",
            "docx",
            "--outdir",
            str(output_directory),
            str(path),
        ]
        environment = os.environ.copy()
        environment["XDG_CACHE_HOME"] = str(cache_directory)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentReadError("LibreOffice timed out while converting legacy DOC.") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise DocumentReadError("Unable to run LibreOffice: {}".format(exc)) from exc
        converted = sorted(output_directory.glob("*.docx"))
        if completed.returncode != 0 or len(converted) != 1:
            detail = (completed.stderr or completed.stdout).strip()
            raise DocumentReadError(
                "LibreOffice could not convert legacy DOC: {}".format(
                    detail or "no DOCX output was created"
                )
            )
        return _extract_docx(converted[0])


def _find_soffice() -> str:
    configured = os.environ.get("SOFFICE_CMD")
    candidates = [
        configured,
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.com",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise MissingDependencyError(
        "LibreOffice is required to convert legacy .doc files. Install LibreOffice "
        "and put 'soffice' on PATH, or set SOFFICE_CMD."
    )


def _extract_docx(path: Path) -> WordSource:
    try:
        import html5lib
        import mammoth
    except ImportError as exc:
        raise MissingDependencyError(
            "DOCX conversion requires the 'mammoth' and 'html5lib' Python packages."
        ) from exc

    title, author, language = _read_core_metadata(path)
    resources: List[EpubResource] = []

    def convert_image(image: object) -> Dict[str, str]:
        media_type = str(getattr(image, "content_type", "")).casefold()
        if media_type not in _IMAGE_MEDIA:
            raise DocumentReadError(
                "The DOCX contains an unsupported image type: {}.".format(media_type or "unknown")
            )
        extension, normalized_media_type = _IMAGE_MEDIA[media_type]
        try:
            with image.open() as image_file:
                content = image_file.read()
        except OSError as exc:
            raise DocumentReadError("Unable to extract a DOCX image: {}".format(exc)) from exc
        filename = "images/word-image-{:03d}{}".format(len(resources) + 1, extension)
        properties = "svg" if normalized_media_type == "image/svg+xml" else ""
        resources.append(
            EpubResource(filename, normalized_media_type, content, properties=properties)
        )
        return {"src": "../{}".format(filename)}

    try:
        with path.open("rb") as source_file:
            result = mammoth.convert_to_html(
                source_file,
                convert_image=mammoth.images.img_element(convert_image),
                external_file_access=False,
                include_embedded_style_map=False,
                id_prefix="word-",
            )
    except DocumentReadError:
        raise
    except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise DocumentReadError("Unable to read DOCX '{}': {}".format(path, exc)) from exc
    except Exception as exc:
        raise DocumentReadError("Unable to convert DOCX '{}': {}".format(path, exc)) from exc

    try:
        fragment = html5lib.parseFragment(result.value, namespaceHTMLElements=False)
        elements = []
        for element in list(fragment):
            sanitized = _sanitize_element(element)
            if sanitized is not None:
                elements.append(ET.tostring(sanitized, encoding="utf-8"))
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise DocumentReadError("Mammoth returned invalid document HTML: {}".format(exc)) from exc

    warnings = tuple(
        str(message.message)
        for message in result.messages
        if str(getattr(message, "type", "")).casefold() == "warning"
    )
    if not elements:
        raise DocumentReadError("The DOCX contains no convertible content.")
    return WordSource(
        tuple(elements),
        tuple(resources),
        title,
        author,
        language,
        warnings,
    )


def _read_core_metadata(path: Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            content = archive.read("docProps/core.xml")
    except KeyError:
        return None, None, None
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentReadError("Unable to read DOCX metadata: {}".format(exc)) from exc
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise DocumentReadError("DOCX core metadata is malformed.") from exc

    def value(xpath: str) -> Optional[str]:
        element = root.find(xpath, _CORE_NAMESPACES)
        if element is None or element.text is None:
            return None
        cleaned = " ".join(element.text.split())
        return cleaned or None

    return value("dc:title"), value("dc:creator"), value("dc:language")


def _sanitize_element(element: ET.Element) -> Optional[ET.Element]:
    tag = _local_name(element.tag).casefold()
    if tag in _DROP_TAGS:
        return None
    if tag not in _ALLOWED_TAGS:
        tag = "div"
    element.tag = tag
    allowed_attributes = _GLOBAL_ATTRIBUTES | _TAG_ATTRIBUTES.get(tag, set())
    for attribute in list(element.attrib):
        local_attribute = _local_name(attribute).casefold()
        if local_attribute not in allowed_attributes or local_attribute.startswith("on"):
            del element.attrib[attribute]
            continue
        if attribute != local_attribute:
            element.attrib[local_attribute] = element.attrib.pop(attribute)
    if tag == "a" and "href" in element.attrib:
        href = element.attrib["href"].strip()
        if not _safe_link(href):
            del element.attrib["href"]
    if tag == "img":
        src = element.attrib.get("src", "")
        if not src.startswith("../images/word-image-"):
            return None
        element.attrib.setdefault("alt", "")
    for child in list(element):
        sanitized = _sanitize_element(child)
        if sanitized is None:
            element.remove(child)
    return element


def _safe_link(href: str) -> bool:
    if href.startswith("#"):
        return True
    parsed = urlsplit(href)
    return parsed.scheme.casefold() in {"http", "https", "mailto"}


def _prepare_navigation(
    nodes: Sequence[ET.Element],
    filename: str,
    body_id: str,
    section_title: str,
) -> Tuple[NavigationEntry, ...]:
    headings = [
        element
        for node in nodes
        for element in node.iter()
        if _local_name(element.tag) in {"h1", "h2", "h3"}
    ]
    used_ids = {body_id}
    mutable_roots: List[_MutableNavigation] = []
    stack: List[_MutableNavigation] = []
    for heading in headings:
        title = " ".join("".join(heading.itertext()).split())
        if not title:
            continue
        heading_id = heading.attrib.get("id") or _slug(title)
        original_id = heading_id
        suffix = 2
        while heading_id in used_ids:
            heading_id = "{}-{}".format(original_id, suffix)
            suffix += 1
        heading.attrib["id"] = heading_id
        used_ids.add(heading_id)
        level = int(_local_name(heading.tag)[1])
        item = _MutableNavigation(title, "{}#{}".format(filename, heading_id), level)
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            stack[-1].children.append(item)
        else:
            mutable_roots.append(item)
        stack.append(item)
    if not mutable_roots:
        return (NavigationEntry(section_title, "{}#{}".format(filename, body_id)),)
    return tuple(_freeze_navigation(item) for item in mutable_roots)


def _freeze_navigation(item: _MutableNavigation) -> NavigationEntry:
    return NavigationEntry(
        item.title,
        item.href,
        tuple(_freeze_navigation(child) for child in item.children),
    )


def _word_xhtml(
    language: str,
    title: str,
    nodes: Sequence[ET.Element],
    body_id: str,
) -> bytes:
    ET.register_namespace("", _XHTML_NS)
    root = ET.Element(
        _qualified(_XHTML_NS, "html"),
        {"lang": language, _qualified(_XML_NS, "lang"): language},
    )
    head = ET.SubElement(root, _qualified(_XHTML_NS, "head"))
    ET.SubElement(head, _qualified(_XHTML_NS, "title")).text = title
    ET.SubElement(
        head,
        _qualified(_XHTML_NS, "link"),
        {"rel": "stylesheet", "type": "text/css", "href": "../styles/book.css"},
    )
    body = ET.SubElement(root, _qualified(_XHTML_NS, "body"), {"id": body_id})
    for node in nodes:
        body.append(deepcopy(node))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _heading_text(nodes: Sequence[ET.Element]) -> Optional[str]:
    for node in nodes:
        if _local_name(node.tag) == "h1":
            value = " ".join("".join(node.itertext()).split())
            if value:
                return value
    return None


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:64] or "heading"


def _local_name(value: str) -> str:
    return str(value).rsplit("}", 1)[-1]


def _qualified(namespace: str, name: str) -> str:
    return "{{{}}}{}".format(namespace, name)
