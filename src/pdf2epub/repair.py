"""Validation-gated conservative repair and semantic EPUB reconstruction."""

import copy
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from .errors import EpubReadError, EpubValidationError, EpubWriteError
from .models import RepairOptions

_XHTML_NS = "http://www.w3.org/1999/xhtml"

_BLOCK_ELEMENTS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "details",
    "dialog",
    "div",
    "dl",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "table",
    "tr",
    "ul",
}
_ROW_CONTAINERS = {"table", "thead", "tbody", "tfoot"}
_DOCTYPE_PATTERN = re.compile(
    rb"<!DOCTYPE(?:[^\[>]+|\[(?:[^\]]|\](?!>))*\])*>",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ValidationSummary:
    """Counts and messages returned by one EPUBCheck run."""

    fatal_count: int
    error_count: int
    warning_count: int
    messages: Tuple[str, ...]


@dataclass(frozen=True)
class RepairOutcome:
    """Private handoff from the repair pipeline to the public API."""

    epub_version: str
    fixes: Tuple[str, ...]
    before: ValidationSummary
    after: ValidationSummary
    warnings: Tuple[str, ...]
    title: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    chapter_count: Optional[int] = None


@dataclass
class _Archive:
    infos: List[zipfile.ZipInfo]
    resources: Dict[str, bytes]
    comment: bytes
    package_path: str
    content_paths: Tuple[str, ...]
    epub_version: str
    needs_archive_normalization: bool


def repair_epub_archive(
    source: Path,
    destination: Path,
    options: RepairOptions,
) -> RepairOutcome:
    """Repair or rebuild and atomically publish an EPUBCheck-clean copy."""

    archive = _read_archive(source)
    epubcheck_command = _resolve_epubcheck_command(options)
    before = _run_epubcheck(epubcheck_command, source)
    repaired_resources, fixes = _repair_resources(archive)
    rebuild_warnings: Tuple[str, ...] = ()
    title: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    chapter_count: Optional[int] = None
    output_version = archive.epub_version
    if options.full_repair:
        from .rebuild import rebuild_publication

        rebuilt = rebuild_publication(archive, repaired_resources)
        repaired_resources = rebuilt.resources
        fixes = [
            fix
            for fix in fixes
            if not fix.startswith("Added empty alt attributes")
            and not fix.startswith("No structural changes were required")
        ]
        fixes.extend(rebuilt.fixes)
        rebuild_warnings = rebuilt.warnings
        title = rebuilt.title
        author = rebuilt.author
        language = rebuilt.language
        chapter_count = rebuilt.chapter_count
        output_version = "3.3"

    temporary_path: Optional[Path] = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=str(destination.parent),
            prefix=".{}.".format(destination.name),
            suffix=".tmp.epub",
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        _write_archive(temporary_path, archive, repaired_resources)
        after = _run_epubcheck(epubcheck_command, temporary_path)
        if after.fatal_count or after.error_count:
            raise EpubValidationError(_remaining_error_message(after))
        if destination.exists() and not options.overwrite:
            raise EpubWriteError("Output file already exists: {}".format(destination))
        os.replace(str(temporary_path), str(destination))
        temporary_path = None
    except (EpubValidationError, EpubWriteError):
        raise
    except OSError as exc:
        raise EpubWriteError(
            "Unable to write repaired EPUB '{}': {}".format(destination, exc)
        ) from exc
    finally:
        if temporary_path is not None:
            with suppress(FileNotFoundError, OSError):
                temporary_path.unlink()

    warnings = rebuild_warnings + tuple(
        message for message in after.messages if message.startswith("WARNING(")
    )
    return RepairOutcome(
        epub_version=output_version,
        fixes=tuple(fixes),
        before=before,
        after=after,
        warnings=warnings,
        title=title,
        author=author,
        language=language,
        chapter_count=chapter_count,
    )


def _read_archive(source: Path) -> _Archive:
    try:
        with zipfile.ZipFile(source, mode="r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise EpubReadError("EPUB archive contains duplicate entry names.")
            for info in infos:
                _validate_member_name(info.filename)
                if info.flag_bits & 0x1:
                    raise EpubReadError("Encrypted EPUB archive entries are not supported.")
            resources = {info.filename: archive.read(info) for info in infos}
            comment = archive.comment
    except EpubReadError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise EpubReadError("Unable to read EPUB archive: {}".format(exc)) from exc

    if "mimetype" not in resources:
        raise EpubReadError("EPUB archive is missing its mimetype entry.")
    if resources["mimetype"] != b"application/epub+zip":
        raise EpubReadError("EPUB mimetype entry is invalid.")
    if "META-INF/container.xml" not in resources:
        raise EpubReadError("EPUB archive is missing META-INF/container.xml.")

    package_path = _package_path(resources)
    package_root = _parse_xml(resources[package_path], package_path)
    if _local_name(package_root) != "package":
        raise EpubReadError("EPUB package document does not have a package root element.")
    epub_version = str(package_root.attrib.get("version", "")).strip()
    if not (epub_version.startswith("2") or epub_version.startswith("3")):
        raise EpubReadError(
            "Unsupported or missing EPUB package version: {!r}".format(epub_version)
        )
    content_paths = _content_document_paths(package_root, package_path, resources)

    mimetype_info = infos[names.index("mimetype")]
    needs_normalization = (
        names[0] != "mimetype" or mimetype_info.compress_type != zipfile.ZIP_STORED
    )
    return _Archive(
        infos=infos,
        resources=resources,
        comment=comment,
        package_path=package_path,
        content_paths=content_paths,
        epub_version=epub_version,
        needs_archive_normalization=needs_normalization,
    )


def _validate_member_name(name: str) -> None:
    normalized = posixpath.normpath(name)
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or normalized == ".."
        or normalized.startswith("../")
    ):
        raise EpubReadError("EPUB archive contains an unsafe entry name: {!r}".format(name))


def _package_path(resources: Mapping[str, bytes]) -> str:
    container = _parse_xml(resources["META-INF/container.xml"], "META-INF/container.xml")
    rootfiles = [
        element
        for element in container.iter()
        if _local_name(element) == "rootfile" and element.attrib.get("full-path")
    ]
    if len(rootfiles) != 1:
        raise EpubReadError(
            "EPUB container must identify exactly one package document; found {}.".format(
                len(rootfiles)
            )
        )
    package_path = str(rootfiles[0].attrib["full-path"])
    _validate_member_name(package_path)
    if package_path not in resources:
        raise EpubReadError(
            "EPUB container references a missing package document: {}".format(package_path)
        )
    return package_path


def _content_document_paths(
    package: ET.Element,
    package_path: str,
    resources: Mapping[str, bytes],
) -> Tuple[str, ...]:
    package_directory = posixpath.dirname(package_path)
    paths: List[str] = []
    for item in package.iter():
        if _local_name(item) != "item" or item.attrib.get("media-type") != "application/xhtml+xml":
            continue
        href = item.attrib.get("href")
        if not href:
            raise EpubReadError("EPUB manifest contains an XHTML item without an href.")
        parts = urlsplit(href)
        if parts.scheme or parts.netloc:
            raise EpubReadError("EPUB manifest contains an external XHTML item: {}".format(href))
        relative_path = unquote(parts.path)
        resolved = posixpath.normpath(posixpath.join(package_directory, relative_path))
        _validate_member_name(resolved)
        if resolved not in resources:
            raise EpubReadError(
                "EPUB manifest references a missing XHTML resource: {}".format(href)
            )
        if resolved not in paths:
            paths.append(resolved)
    if not paths:
        raise EpubReadError("EPUB package does not manifest any XHTML content documents.")
    return tuple(paths)


def _repair_resources(archive: _Archive) -> Tuple[Dict[str, bytes], List[str]]:
    resources = dict(archive.resources)
    fix_counts = {
        "guides": 0,
        "images": 0,
        "spans": 0,
        "nested_rows": 0,
        "orphan_rows": 0,
        "tables": 0,
    }

    package_content, removed_guides = _remove_empty_guide(
        resources[archive.package_path],
        archive.package_path,
        archive.epub_version,
    )
    if removed_guides:
        resources[archive.package_path] = package_content
        fix_counts["guides"] = removed_guides

    for path in archive.content_paths:
        repaired, counts = _repair_xhtml(
            resources[path],
            path,
            require_block_body=archive.epub_version.startswith("2"),
        )
        if any(counts.values()):
            resources[path] = repaired
        for name, count in counts.items():
            fix_counts[name] += count

    fixes = _fix_descriptions(fix_counts)
    if archive.needs_archive_normalization:
        fixes.append("Normalized the position and compression of the EPUB mimetype entry.")
    if not fixes:
        fixes.append("No structural changes were required; created a validated copy.")
    return resources, fixes


def _remove_empty_guide(
    content: bytes,
    path: str,
    epub_version: str,
) -> Tuple[bytes, int]:
    if not epub_version.startswith("2"):
        return content, 0
    root = _parse_xml(content, path)
    for guide in list(root):
        if _local_name(guide) == "guide" and not list(guide) and not (guide.text or "").strip():
            _remove_preserving_tail(root, guide)
            return _serialize_xml(root, content), 1
    return content, 0


def _repair_xhtml(
    content: bytes,
    path: str,
    *,
    require_block_body: bool,
) -> Tuple[bytes, Dict[str, int]]:
    root = _parse_xml(content, path)
    if _local_name(root) != "html":
        raise EpubReadError("{} does not have an html root element.".format(path))
    counts = {
        "images": 0,
        "spans": 0,
        "nested_rows": 0,
        "orphan_rows": 0,
        "tables": 0,
    }
    for element in root.iter():
        if _local_name(element) == "img" and "alt" not in element.attrib:
            element.set("alt", "")
            counts["images"] += 1

    counts["nested_rows"] = _unwrap_nested_table_rows(root)
    orphan_rows, tables = _wrap_orphan_table_rows(root)
    counts["orphan_rows"] = orphan_rows
    counts["tables"] = tables
    counts["spans"] = _replace_invalid_spans(
        root,
        require_block_body=require_block_body,
    )

    if not any(counts.values()):
        return content, counts
    return _serialize_xml(root, content), counts


def _unwrap_nested_table_rows(root: ET.Element) -> int:
    moved_rows = 0
    for table in [element for element in root.iter() if _local_name(element) == "table"]:
        index = 0
        while index < len(table):
            wrapper_row = table[index]
            wrapper_children = list(wrapper_row)
            if _local_name(wrapper_row) != "tr" or len(wrapper_children) != 1:
                index += 1
                continue
            wrapper_cell = wrapper_children[0]
            nested_rows = list(wrapper_cell)
            safe_cell_attributes = set(wrapper_cell.attrib).issubset({"class"})
            if (
                _local_name(wrapper_cell) != "td"
                or not nested_rows
                or wrapper_row.attrib
                or not safe_cell_attributes
                or (wrapper_row.text or "").strip()
                or (wrapper_cell.text or "").strip()
                or any(_local_name(row) != "tr" for row in nested_rows)
            ):
                index += 1
                continue

            wrapper_tail = wrapper_row.tail
            table.remove(wrapper_row)
            for offset, row in enumerate(nested_rows):
                wrapper_cell.remove(row)
                table.insert(index + offset, row)
            if wrapper_tail:
                nested_rows[-1].tail = (nested_rows[-1].tail or "") + wrapper_tail
            moved_rows += len(nested_rows)
            index += len(nested_rows)
    return moved_rows


def _wrap_orphan_table_rows(root: ET.Element) -> Tuple[int, int]:
    table_class = _existing_table_class(root)
    wrapped_rows = 0
    created_tables = 0
    for parent in list(root.iter()):
        if _local_name(parent) in _ROW_CONTAINERS:
            continue
        index = 0
        while index < len(parent):
            if _local_name(parent[index]) != "tr":
                index += 1
                continue
            rows: List[ET.Element] = []
            while index + len(rows) < len(parent):
                candidate = parent[index + len(rows)]
                if _local_name(candidate) != "tr":
                    break
                rows.append(candidate)
            namespace = _namespace(rows[0])
            attributes = {"class": table_class} if table_class else {}
            table = ET.Element(_qualified(namespace, "table"), attributes)
            table.tail = rows[-1].tail
            rows[-1].tail = None
            for row in rows:
                parent.remove(row)
                table.append(row)
            parent.insert(index, table)
            wrapped_rows += len(rows)
            created_tables += 1
            index += 1
    return wrapped_rows, created_tables


def _existing_table_class(root: ET.Element) -> Optional[str]:
    for element in root.iter():
        if _local_name(element) == "table" and element.attrib.get("class"):
            return element.attrib["class"]
    return None


def _replace_invalid_spans(root: ET.Element, *, require_block_body: bool) -> int:
    replacements = 0
    while True:
        changed = False
        parents = {child: parent for parent in root.iter() for child in parent}
        for element in reversed(list(root.iter())):
            if _local_name(element) != "span":
                continue
            parent = parents.get(element)
            direct_block_child = any(
                _local_name(child) in _BLOCK_ELEMENTS for child in list(element)
            )
            invalid_body_child = (
                require_block_body and parent is not None and _local_name(parent) == "body"
            )
            if direct_block_child or invalid_body_child:
                element.tag = _qualified(_namespace(element), "div")
                replacements += 1
                changed = True
        if not changed:
            return replacements


def _remove_preserving_tail(parent: ET.Element, child: ET.Element) -> None:
    index = list(parent).index(child)
    tail = child.tail
    parent.remove(child)
    if not tail:
        return
    if index:
        previous = parent[index - 1]
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail


def _fix_descriptions(counts: Mapping[str, int]) -> List[str]:
    descriptions = []
    if counts["guides"]:
        descriptions.append("Removed {} empty EPUB 2 guide.".format(counts["guides"]))
    if counts["images"]:
        descriptions.append("Added empty alt attributes to {} images.".format(counts["images"]))
    if counts["spans"]:
        descriptions.append(
            "Changed {} invalid span wrappers to div elements.".format(counts["spans"])
        )
    if counts["nested_rows"]:
        descriptions.append(
            "Moved {} nested rows into their containing tables.".format(counts["nested_rows"])
        )
    if counts["orphan_rows"]:
        descriptions.append(
            "Wrapped {} orphan rows in {} tables.".format(counts["orphan_rows"], counts["tables"])
        )
    return descriptions


def _write_archive(
    destination: Path,
    archive: _Archive,
    resources: Mapping[str, bytes],
) -> None:
    try:
        mimetype_info = copy.copy(
            next(info for info in archive.infos if info.filename == "mimetype")
        )
        with zipfile.ZipFile(
            destination,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as output:
            output.comment = archive.comment
            output.writestr(
                mimetype_info,
                resources["mimetype"],
                compress_type=zipfile.ZIP_STORED,
            )
            for original_info in archive.infos:
                if original_info.filename == "mimetype":
                    continue
                if original_info.filename not in resources:
                    continue
                info = copy.copy(original_info)
                compression = zipfile.ZIP_STORED if info.is_dir() else zipfile.ZIP_DEFLATED
                output.writestr(
                    info,
                    resources[info.filename],
                    compress_type=compression,
                )
            original_names = {info.filename for info in archive.infos}
            for name in sorted(set(resources) - original_names):
                output.writestr(
                    name,
                    resources[name],
                    compress_type=zipfile.ZIP_DEFLATED,
                )
    except (OSError, StopIteration, ValueError, zipfile.BadZipFile) as exc:
        raise EpubWriteError("Unable to assemble repaired EPUB: {}".format(exc)) from exc


def _resolve_epubcheck_command(options: RepairOptions) -> Tuple[str, ...]:
    configured_jar = options.epubcheck_jar
    if configured_jar is None:
        configured_jar = os.environ.get("EPUBCHECK_JAR")
    if configured_jar is not None:
        jar = Path(configured_jar).expanduser()
        try:
            jar = jar.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise EpubValidationError(
                "EPUBCheck jar does not exist: {}".format(configured_jar)
            ) from exc
        if not jar.is_file():
            raise EpubValidationError("EPUBCheck jar is not a file: {}".format(jar))
        java = _resolve_java()
        return (java, "-jar", str(jar))

    executable = shutil.which("epubcheck")
    if executable:
        return (executable,)
    raise EpubValidationError(
        "EPUBCheck is required for EPUB repair. Use --epubcheck-jar, set "
        "EPUBCHECK_JAR, or install an epubcheck executable on PATH."
    )


def _resolve_java() -> str:
    candidates = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(str(Path(java_home).expanduser() / "bin" / "java"))
    candidates.extend(
        [
            "/opt/homebrew/opt/openjdk/bin/java",
            "/usr/local/opt/openjdk/bin/java",
        ]
    )
    path_java = shutil.which("java")
    if path_java:
        candidates.append(path_java)
    for candidate in candidates:
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise EpubValidationError(
        "Java is required to run the configured EPUBCheck jar. Set JAVA_HOME or "
        "install java on PATH."
    )


def _run_epubcheck(command: Sequence[str], publication: Path) -> ValidationSummary:
    descriptor, report_name = tempfile.mkstemp(prefix="pdf2epub-epubcheck-", suffix=".json")
    os.close(descriptor)
    report_path = Path(report_name)
    try:
        try:
            completed = subprocess.run(
                list(command) + ["--json", str(report_path), str(publication)],
                text=True,
                capture_output=True,
                check=False,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise EpubValidationError("Unable to run EPUBCheck: {}".format(exc)) from exc
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            details = (completed.stderr or completed.stdout).strip()
            if details:
                details = " {}".format(details.splitlines()[-1])
            raise EpubValidationError(
                "EPUBCheck did not produce a readable JSON report.{}".format(details)
            ) from exc
        summary = _validation_summary(report)
        if completed.returncode not in (0, 1):
            details = (completed.stderr or completed.stdout).strip()
            if details:
                details = " {}".format(details.splitlines()[-1])
            raise EpubValidationError(
                "EPUBCheck exited unexpectedly with status {}.{}".format(
                    completed.returncode,
                    details,
                )
            )
        if completed.returncode == 1 and not (summary.fatal_count or summary.error_count):
            raise EpubValidationError("EPUBCheck failed without reporting a fatal error or error.")
        return summary
    finally:
        with suppress(FileNotFoundError, OSError):
            report_path.unlink()


def _validation_summary(report: Mapping[str, object]) -> ValidationSummary:
    totals = {"FATAL": 0, "ERROR": 0, "WARNING": 0}
    rendered_messages: List[str] = []
    raw_messages = report.get("messages", [])
    if not isinstance(raw_messages, list):
        raise EpubValidationError("EPUBCheck JSON report has an invalid messages field.")
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        severity = str(raw_message.get("severity", "")).upper()
        locations = raw_message.get("locations", [])
        location_count = len(locations) if isinstance(locations, list) else 0
        try:
            additional = int(raw_message.get("additionalLocations", 0) or 0)
        except (TypeError, ValueError):
            additional = 0
        occurrences = max(1, location_count + additional)
        if severity in totals:
            totals[severity] += occurrences
        identifier = str(raw_message.get("ID", "EPUBCHECK"))
        message = str(raw_message.get("message", "Validation message"))
        rendered_messages.append(
            "{}({}): {} [{} occurrence{}]".format(
                severity,
                identifier,
                message,
                occurrences,
                "" if occurrences == 1 else "s",
            )
        )
    return ValidationSummary(
        fatal_count=totals["FATAL"],
        error_count=totals["ERROR"],
        warning_count=totals["WARNING"],
        messages=tuple(rendered_messages),
    )


def _remaining_error_message(summary: ValidationSummary) -> str:
    details = [
        message
        for message in summary.messages
        if message.startswith("FATAL(") or message.startswith("ERROR(")
    ]
    suffix = ""
    if details:
        suffix = " {}".format(" | ".join(details[:5]))
    return (
        "Repaired EPUB still has {} fatal errors and {} errors; no output was created.{}"
    ).format(summary.fatal_count, summary.error_count, suffix)


def _parse_xml(content: bytes, path: str) -> ET.Element:
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        return ET.fromstring(content, parser=parser)
    except (ET.ParseError, ValueError) as exc:
        raise EpubReadError("Unable to parse EPUB XML resource '{}': {}".format(path, exc)) from exc


def _serialize_xml(root: ET.Element, original: bytes) -> bytes:
    _register_namespaces(original)
    content = ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )
    doctype = _DOCTYPE_PATTERN.search(original)
    if doctype:
        declaration, body = content.split(b"?>", 1)
        content = declaration + b"?>\n" + doctype.group(0) + body
    return content


def _register_namespaces(content: bytes) -> None:
    try:
        declarations: Iterable[Tuple[str, str]] = (
            value for _event, value in ET.iterparse(io.BytesIO(content), events=("start-ns",))
        )
        for prefix, uri in declarations:
            if prefix != "xml":
                ET.register_namespace(prefix or "", uri)
    except (ET.ParseError, ValueError):
        return


def _local_name(element: ET.Element) -> str:
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _namespace(element: ET.Element) -> str:
    tag = element.tag
    if isinstance(tag, str) and tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return _XHTML_NS


def _qualified(namespace: str, name: str) -> str:
    return "{{{}}}{}".format(namespace, name)
