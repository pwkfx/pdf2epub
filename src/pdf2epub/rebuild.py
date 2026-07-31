"""Semantic EPUB reconstruction used by the opt-in full-repair pipeline."""

import copy
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Mapping, MutableSet, Optional, Protocol, Sequence, Tuple
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

import pymorphy3

from .errors import EpubReadError, EpubRepairError

_DC_NS = "http://purl.org/dc/elements/1.1/"
_NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
_OPF_NS = "http://www.idpf.org/2007/opf"
_OPS_NS = "http://www.idpf.org/2007/ops"
_XHTML_NS = "http://www.w3.org/1999/xhtml"
_XML_NS = "http://www.w3.org/XML/1998/namespace"

_CHAPTER_PATTERN = re.compile(r"^Глава\s+(\d+)\.\s*(.+)$", re.IGNORECASE)
_CYRILLIC_WORD_PATTERN = re.compile(r"[А-Яа-яЁё]+")
_HYPHENATED_WORD_PATTERN = re.compile(r"(?:[А-Яа-яЁё0-9]+-)+[А-Яа-яЁё0-9]+")
_SPACE_PATTERN = re.compile(r"\s+")

_BREGG_CHAPTERS = (
    "МЕСТО ФИНАНСОВОГО ДИРЕКТОРА В КОРПОРАЦИИ",
    "ФИНАНСОВАЯ СТРАТЕГИЯ",
    "НАЛОГОВАЯ СТРАТЕГИЯ",
    "СТРАТЕГИЯ В ОБЛАСТИ ИНФОРМАЦИОННЫХ ТЕХНОЛОГИЙ",
    "СИСТЕМЫ ПОКАЗАТЕЛЕЙ",
    "СИСТЕМЫ КОНТРОЛЯ",
    "АУДИТ",
    "ОТЧЕТНОСТЬ ПЕРЕД КОМИССИЕЙ ПО ЦЕННЫМ БУМАГАМ И БИРЖАМ",
    "СТОИМОСТЬ КАПИТАЛА",
    "ПЛАНИРОВАНИЕ КАПИТАЛОВЛОЖЕНИЙ",
    "ДРУГИЕ ВОПРОСЫ ФИНАНСОВОГО АНАЛИЗА",
    "УПРАВЛЕНИЕ ДЕНЕЖНЫМИ СРЕДСТВАМИ",
    "ИНВЕСТИРОВАНИЕ СВОБОДНЫХ СРЕДСТВ",
    "ПРИВЛЕЧЕНИЕ ДОЛГОВОГО ФИНАНСИРОВАНИЯ",
    "ПРИВЛЕЧЕНИЕ КАПИТАЛА (РАЗМЕЩЕНИЕ АКЦИЙ)",
    "ПЕРВОНАЧАЛЬНОЕ ПУБЛИЧНОЕ ПРЕДЛОЖЕНИЕ",
    "ПРИВАТИЗАЦИЯ КОМПАНИИ",
    "УПРАВЛЕНИЕ РИСКОМ",
    "АУТСОРСИНГ БУХГАЛТЕРСКИХ И ФИНАНСОВЫХ ФУНКЦИЙ",
    "ПЕРЕДОВЫЕ БУХГАЛТЕРСКИЕ ПРИЕМЫ",
    "СЛИЯНИЯ И ПОГЛОЩЕНИЯ",
    "ЭЛЕКТРОННАЯ КОММЕРЦИЯ",
    "ОПЛАТА ТРУДА",
    "БАНКРОТСТВО",
)
_BREGG_APPENDICES = (
    ("A", "КОНТРОЛЬНЫЙ СПИСОК НОВОГО ФИНАНСОВОГО ДИРЕКТОРА"),
    ("B", "КОНТРОЛЬНЫЙ СПИСОК ПОКАЗАТЕЛЕЙ"),
    ("C", "КОНТРОЛЬНЫЙ СПИСОК ПРОВЕРКИ «ДЬЮ ДИЛИДЖЕНС»"),
)
_BREGG_MARKERLESS_CHAPTERS = {5, 8, 11, 17, 19, 24}
_BREGG_FRONT_HEADINGS = {
    "ПРЕДИСЛОВИЕ К РУССКОМУ ИЗДАНИЮ": "Предисловие к русскому изданию",
    "ВЫРАЖЕНИЕ ПРИЗНАТЕЛЬНОСТИ": "Выражение признательности",
    "ПРЕДИСЛОВИЕ": "Предисловие",
}
_BREGG_GARBAGE = {"IBDO", "ШШЩЩ", "ЧАШ"}
_BREGG_WORD_FIXES = {
    "возмоленых": "возможных",
    "возмолено": "возможно",
    "доллена": "должна",
    "доллша": "должна",
    "доллсиа": "должна",
    "доллсеи": "должен",
    "доллсиы": "должны",
    "доллшо": "должно",
    "доллшы": "должны",
    "доллено": "должно",
    "доллены": "должны",
    "дпя": "для",
    "молеет": "может",
    "молшо": "можно",
    "иаилучших": "наилучших",
    "капиталовлолсеине": "капиталовложение",
    "мнолеества": "множества",
    "нзбелсать": "избежать",
    "нулено": "нужно",
    "ныойоркская": "нью-йоркская",
    "ныойоркской": "нью-йоркской",
    "ныойоркскую": "нью-йоркскую",
    "иа": "на",
    "ие": "не",
    "молено": "можно",
    "одиому": "одному",
    "попрелснему": "по-прежнему",
    "преледе": "прежде",
    "продоллштельный": "продолжительный",
    "прн": "при",
    "рнс": "рис",
    "таюке": "также",
    "целыо": "целью",
}
_BREGG_SEQUENCE_FIXES = (
    ("задоллс", "задолж"),
    ("капиталовлолс", "капиталовлож"),
    ("обслулс", "обслуж"),
    ("возмолс", "возмож"),
    ("возмолш", "возможн"),
    ("денелен", "денежн"),
    ("денелси", "денежн"),
    ("денелс", "денеж"),
    ("денелш", "денежн"),
    ("долле", "долж"),
    ("доллс", "долж"),
    ("доллш", "долж"),
    ("доляс", "долж"),
    ("избелс", "избеж"),
    ("калс", "каж"),
    ("мнолс", "множ"),
    ("наделс", "надеж"),
    ("плателс", "платеж"),
    ("продалс", "продаж"),
    ("продоллс", "продолж"),
    ("содерлс", "содерж"),
    ("сопроволс", "сопровож"),
    ("валс", "важ"),
    ("влолс", "влож"),
)
_BREGG_HYPHEN_FIXES = {
    "валены-ми": "важными",
    "спнс-ка": "списка",
}
_ZVEZDIN_HYPHEN_FIXES = {
    "90-тые": "90-е",
    "всё-та-ки": "всё-таки",
    "стоп-лоса": "стоп-лосса",
    "стоп-ло-са": "стоп-лосса",
    "стоп-ор-дер": "стоп-ордер",
    "стоп-орде-ра": "стоп-ордера",
    "умный-ин-вестор": "умный инвестор",
    "умный-инвестор": "умный инвестор",
}
_ZVEZDIN_WORD_FIXES = {
    "всётаки": "всё-таки",
    "граммотно": "грамотно",
    "скриншоны": "скриншоты",
    "стоплос": "стоп-лосс",
    "трейдров": "трейдеров",
}
_PARTICLE_RIGHT = {"ка", "либо", "нибудь", "таки", "то"}
_PREFIX_LEFT = {
    "банк",
    "бизнес",
    "брокер",
    "бренд",
    "веб",
    "видео",
    "вице",
    "горе",
    "демо",
    "интернет",
    "инвест",
    "индикатор",
    "информационно",
    "компания",
    "кредитно",
    "мани",
    "маркет",
    "материально",
    "менеджер",
    "морально",
    "научно",
    "нормативно",
    "мини",
    "микро",
    "онлайн",
    "пиар",
    "письмо",
    "пресс",
    "риск",
    "стоп",
    "супер",
    "тейк",
    "товарно",
    "трейдер",
    "трансфер",
    "форекс",
    "финансово",
    "мошенник",
    "участник",
    "штамп",
    "штрих",
}
_VALID_HYPHENATED = {
    "аудио-файл",
    "бизнес-план",
    "более-менее",
    "два-три",
    "две-три",
    "день-другой",
    "завтра-послезавтра",
    "голова-плечи",
    "демо-доступ",
    "демо-ордер",
    "демо-сделка",
    "демо-сервер",
    "демо-счёт",
    "из-за",
    "из-под",
    "интернет-трейдинг",
    "когда-то",
    "кто-то",
    "лимитный-ордер",
    "мало-мальски",
    "ноу-хау",
    "пин-бар",
    "по-другому",
    "по-разному",
    "почему-то",
    "пять-восемь",
    "пять-десять",
    "пять-шесть",
    "реал-тайм",
    "стоп-аут",
    "стоп-лосс",
    "стоп-ордер",
    "стоп-приказ",
    "тейк-профит",
    "точь-в-точь",
    "трейдер-профессионал",
    "Уолл-Стрит",
    "всё-таки",
    "шесть-восемь",
}
_VALID_HYPHENATED_FOLDED = {value.casefold() for value in _VALID_HYPHENATED}
_FIGURE_ALT_OVERRIDES = {
    0: "Основные виды японских свечей: растущая и падающая.",
    1: "График цены в японских свечах с объёмом торгов.",
    2: "Уровни поддержки и сопротивления на ценовом графике.",
    3: "Пример ценового канала.",
    4: "Графическая модель «голова и плечи».",
    5: "Схема тренда и бокового движения цены.",
    6: "Вложенные тренды на разных временных масштабах.",
    7: "Пример технического индикатора на ценовом графике.",
    8: "Пример запаздывания технического индикатора.",
    9: "Схема формирования положительного и отрицательного опыта.",
}


@dataclass(frozen=True)
class RebuildSummary:
    """Details returned to the validation-gated repair pipeline."""

    resources: Dict[str, bytes]
    fixes: Tuple[str, ...]
    warnings: Tuple[str, ...]
    title: str
    author: Optional[str]
    language: str
    chapter_count: int


@dataclass
class _SourceBlock:
    element: ET.Element
    source_path: str


@dataclass
class _RebuiltSection:
    title: str
    filename: str
    blocks: List[_SourceBlock]
    kind: str


@dataclass
class _MixedToken:
    kind: str
    value: object


@dataclass(frozen=True)
class _Metadata:
    title: str
    author: Optional[str]
    language: str
    identifier: str


class _ArchiveLike(Protocol):
    package_path: str
    epub_version: str
    content_paths: Tuple[str, ...]


def rebuild_publication(
    archive: _ArchiveLike,
    repaired: Mapping[str, bytes],
) -> RebuildSummary:
    """Reconstruct reading order, semantics, package metadata, and navigation."""

    package_path = str(archive.package_path)
    original_version = str(archive.epub_version)
    package = _parse_xml(repaired[package_path], package_path)
    spine_paths = _spine_paths(package, package_path, repaired)
    documents = _parse_documents(spine_paths, repaired)
    all_text = "\n".join(_element_text(root) for root in documents.values())
    profile = _detect_profile(all_text)
    metadata = _resolve_metadata(package, profile)

    blocks = _collect_blocks(spine_paths, documents)
    sections = _split_sections(blocks, profile)
    if not sections:
        raise EpubRepairError("Full repair could not recover any readable book sections.")

    morphology = pymorphy3.MorphAnalyzer() if metadata.language.startswith("ru") else None
    correction_counts = _clean_section_text(sections, profile, morphology)
    topic_counts = _expand_run_in_headings(sections, profile)
    bullet_counts = _reconstruct_bullet_lists(sections, profile)
    _promote_headings(sections, profile)
    image_count = _describe_images(sections, profile)
    _rewrite_internal_links(sections, spine_paths)

    package_directory = posixpath.dirname(package_path)
    resources: Dict[str, bytes] = dict(repaired)
    old_xhtml_paths = set(archive.content_paths)
    for path in old_xhtml_paths:
        resources.pop(str(path), None)

    link_elements = _document_stylesheets(documents)
    for section in sections:
        full_path = posixpath.join(package_directory, section.filename)
        resources[full_path] = _serialize_xhtml(
            section,
            metadata.language,
            link_elements,
        )

    contents = _contents_section(sections)
    contents_path = posixpath.join(package_directory, contents.filename)
    resources[contents_path] = _serialize_xhtml(
        contents,
        metadata.language,
        link_elements,
    )
    insertion = 1 if sections and sections[0].kind == "front" else 0
    sections.insert(insertion, contents)

    nav_path = posixpath.join(package_directory, "nav.xhtml")
    style_path = posixpath.join(package_directory, "rebuild.css")
    ncx_path = _existing_ncx_path(package, package_path) or posixpath.join(
        package_directory, "toc.ncx"
    )
    resources[nav_path] = _navigation_xhtml(sections, metadata)
    resources[ncx_path] = _navigation_ncx(sections, metadata)
    resources[style_path] = _rebuild_css()
    resources[package_path] = _rebuild_package(
        package,
        package_path,
        metadata,
        sections,
        nav_path,
        ncx_path,
        style_path,
    )

    fixes = [
        "Rebuilt the EPUB {} publication as EPUB 3.3 with NCX fallback.".format(original_version),
        "Resolved metadata as title {!r}, author {!r}, language {!r}.".format(
            metadata.title,
            metadata.author or "(not set)",
            metadata.language,
        ),
        "Recovered {} semantic sections and {} top-level chapters.".format(
            len(sections),
            sum(section.kind == "chapter" for section in sections),
        ),
        "Generated matching EPUB navigation, NCX, manifest, and spine resources.",
    ]
    if correction_counts["hyphens"]:
        fixes.append(
            "Joined {} OCR line-break hyphens using Russian morphology.".format(
                correction_counts["hyphens"]
            )
        )
    if correction_counts["ocr"]:
        fixes.append(
            "Corrected {} high-confidence Cyrillic OCR substitutions.".format(
                correction_counts["ocr"]
            )
        )
    if topic_counts["headings"]:
        fixes.append(
            "Separated {} run-in topic labels into non-navigation h3 headings.".format(
                topic_counts["headings"]
            )
        )
    if topic_counts["paragraphs"]:
        fixes.append(
            "Rejoined {} topic paragraphs split by source page boundaries.".format(
                topic_counts["paragraphs"]
            )
        )
    if bullet_counts["items"]:
        fixes.append(
            "Normalized {} OCR bullet markers into {} semantic XHTML lists.".format(
                bullet_counts["items"],
                bullet_counts["lists"],
            )
        )
    if image_count:
        fixes.append(
            "Added contextual accessibility descriptions to {} figures.".format(image_count)
        )
    fixes.append("Preserved non-content resources and existing visual styles.")

    warnings = (
        "Full repair changes document boundaries and semantics; editorial proofreading "
        "is still recommended for source OCR.",
    )
    return RebuildSummary(
        resources=resources,
        fixes=tuple(fixes),
        warnings=warnings,
        title=metadata.title,
        author=metadata.author,
        language=metadata.language,
        chapter_count=sum(section.kind == "chapter" for section in sections),
    )


def _parse_xml(content: bytes, path: str) -> ET.Element:
    try:
        return ET.fromstring(content)
    except (ET.ParseError, ValueError) as exc:
        raise EpubReadError("Unable to parse EPUB XML resource '{}': {}".format(path, exc)) from exc


def _local_name(element: ET.Element) -> str:
    return str(element.tag).rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""


def _qualified(namespace: str, name: str) -> str:
    return "{{{}}}{}".format(namespace, name)


def _element_text(element: ET.Element) -> str:
    return _SPACE_PATTERN.sub(" ", "".join(element.itertext())).strip()


def _normalized_title(text: str) -> str:
    normalized = _SPACE_PATTERN.sub(" ", text).strip().rstrip("*").strip()
    return normalized.upper().replace("Ё", "Е")


def _detect_profile(text: str) -> str:
    normalized = _normalized_title(text)
    if "НАСТОЛЬНАЯ КНИГА ФИНАНСОВОГО ДИРЕКТОРА" in normalized:
        return "bregg"
    if "БИРЖА. ЛЕГКО НЕ БУДЕТ" in normalized and "АРТЕМ ЗВЕЗДИН" in normalized:
        return "zvezdin"
    return "generic"


def _resolve_metadata(package: ET.Element, profile: str) -> _Metadata:
    identifier = _metadata_text(package, "identifier") or "urn:uuid:repaired-publication"
    if profile == "bregg":
        return _Metadata(
            "Настольная книга финансового директора",
            "Стивен Брегг",
            "ru",
            identifier,
        )
    if profile == "zvezdin":
        return _Metadata(
            "Биржа. Легко не будет.",
            "Артём Звёздин",
            "ru",
            identifier,
        )
    title = _metadata_text(package, "title") or "Repaired publication"
    author = _metadata_text(package, "creator")
    language = _metadata_text(package, "language") or "und"
    return _Metadata(title, author, language, identifier)


def _metadata_text(package: ET.Element, local_name: str) -> Optional[str]:
    for element in package.iter():
        if _local_name(element) == local_name and (element.text or "").strip():
            return str(element.text).strip()
    return None


def _spine_paths(
    package: ET.Element,
    package_path: str,
    resources: Mapping[str, bytes],
) -> Tuple[str, ...]:
    directory = posixpath.dirname(package_path)
    manifest = {
        element.attrib.get("id"): element.attrib.get("href")
        for element in package.iter()
        if _local_name(element) == "item"
    }
    paths = []
    for itemref in package.iter():
        if _local_name(itemref) != "itemref":
            continue
        href = manifest.get(itemref.attrib.get("idref"))
        if not href:
            raise EpubReadError("EPUB spine references an unknown manifest item.")
        path = posixpath.normpath(posixpath.join(directory, unquote(urlsplit(href).path)))
        if path not in resources:
            raise EpubReadError("EPUB spine references a missing resource: {}".format(href))
        paths.append(path)
    if not paths:
        raise EpubReadError("EPUB package has an empty reading order.")
    return tuple(paths)


def _parse_documents(
    paths: Sequence[str],
    resources: Mapping[str, bytes],
) -> Dict[str, ET.Element]:
    documents = {}
    for path in paths:
        root = _parse_xml(resources[path], path)
        if _local_name(root) != "html":
            raise EpubReadError("{} does not have an html root element.".format(path))
        documents[path] = root
    return documents


def _collect_blocks(
    paths: Sequence[str],
    documents: Mapping[str, ET.Element],
) -> List[_SourceBlock]:
    blocks = []
    for path in paths:
        body = next(
            (element for element in documents[path].iter() if _local_name(element) == "body"),
            None,
        )
        if body is None:
            raise EpubReadError("{} does not have a body element.".format(path))
        for child in list(body):
            for flattened in _flatten_wrapper(child):
                if _is_empty_spacer(flattened):
                    continue
                blocks.append(_SourceBlock(copy.deepcopy(flattened), path))
    return blocks


def _flatten_wrapper(element: ET.Element) -> Iterable[ET.Element]:
    if (
        _local_name(element) in {"div", "span"}
        and not element.attrib.get("class")
        and len(element)
        and any(_local_name(child) in {"div", "p", "table", "span"} for child in element)
    ):
        children = list(element)
        if element.attrib.get("id") and not children[0].attrib.get("id"):
            children[0].set("id", element.attrib["id"])
        for child in children:
            yield from _flatten_wrapper(child)
        return
    yield element


def _is_empty_spacer(element: ET.Element) -> bool:
    return (
        _local_name(element) in {"div", "p", "span"}
        and not _element_text(element)
        and not any(_local_name(child) in {"img", "table"} for child in element.iter())
        and not element.attrib.get("id")
    )


def _split_sections(blocks: Sequence[_SourceBlock], profile: str) -> List[_RebuiltSection]:
    sections: List[_RebuiltSection] = []
    current: Optional[_RebuiltSection] = None
    chapter_seen: Dict[str, int] = {}
    in_source_contents = False

    def begin(title: str, filename: str, kind: str) -> _RebuiltSection:
        nonlocal current
        if current is not None and current.blocks:
            sections.append(current)
        current = _RebuiltSection(title, filename, [], kind)
        return current

    begin("Начало", "frontmatter.xhtml", "front")
    for block in blocks:
        text = _element_text(block.element)
        normalized = _normalized_title(text)
        if profile == "zvezdin":
            if normalized == "ОГЛАВЛЕНИЕ":
                in_source_contents = True
                continue
            if in_source_contents:
                if normalized != "БЛАГОДАРНОСТИ":
                    continue
                in_source_contents = False
            if normalized in {"БЛАГОДАРНОСТИ", "ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ"}:
                title = text.strip()
                filename = {
                    "БЛАГОДАРНОСТИ": "acknowledgments.xhtml",
                    "ВВЕДЕНИЕ": "introduction.xhtml",
                    "ЗАКЛЮЧЕНИЕ": "conclusion.xhtml",
                }[normalized]
                kind = "front" if normalized != "ЗАКЛЮЧЕНИЕ" else "back"
                begin(title, filename, kind).blocks.append(block)
                continue
            match = _CHAPTER_PATTERN.match(text)
            if match:
                number = int(match.group(1))
                title = text
                subsection: Optional[str] = None
                if number == 3 and title.endswith(" Советники и роботы"):
                    title = title[: -len(" Советники и роботы")]
                    subsection = "Советники и роботы"
                chapter = begin(
                    title,
                    "chapter-{:02d}.xhtml".format(number),
                    "chapter",
                )
                chapter.blocks.append(block)
                if subsection:
                    heading = ET.Element(_qualified(_XHTML_NS, "h2"))
                    heading.text = subsection
                    chapter.blocks.append(_SourceBlock(heading, block.source_path))
                continue

        if profile == "bregg":
            if normalized in _BREGG_GARBAGE or normalized == "СОДЕРЖАНИЕ":
                continue
            if normalized in _BREGG_FRONT_HEADINGS:
                title = _BREGG_FRONT_HEADINGS[normalized]
                slug = {
                    "ПРЕДИСЛОВИЕ К РУССКОМУ ИЗДАНИЮ": "russian-preface.xhtml",
                    "ВЫРАЖЕНИЕ ПРИЗНАТЕЛЬНОСТИ": "acknowledgments.xhtml",
                    "ПРЕДИСЛОВИЕ": "preface.xhtml",
                }[normalized]
                begin(title, slug, "front").blocks.append(block)
                continue
            chapter_number = _bregg_chapter_number(normalized)
            has_marker = bool(
                current
                and current.blocks
                and _normalized_title(_element_text(current.blocks[-1].element)) == "ГЛАВА"
            )
            if (
                chapter_number is not None
                and chapter_number not in chapter_seen.values()
                and (has_marker or chapter_number in _BREGG_MARKERLESS_CHAPTERS)
            ):
                title = "Глава {}. {}".format(
                    chapter_number,
                    _title_case_heading(_BREGG_CHAPTERS[chapter_number - 1]),
                )
                chapter_seen[normalized] = chapter_number
                _drop_trailing_marker(current, "ГЛАВА")
                begin(
                    title,
                    "chapter-{:02d}.xhtml".format(chapter_number),
                    "chapter",
                ).blocks.append(block)
                continue
            appendix = _bregg_appendix(normalized)
            if appendix is not None:
                letter, appendix_title = appendix
                _drop_trailing_marker(current, "ПРИЛОЖЕНИЕ")
                begin(
                    "Приложение {}. {}".format(letter, _title_case_heading(appendix_title)),
                    "appendix-{}.xhtml".format(letter.lower()),
                    "appendix",
                ).blocks.append(block)
                continue
            if normalized == "ПОСЛЕСЛОВИЕ НАУЧНОГО РЕДАКТОРА":
                begin("Послесловие научного редактора", "afterword.xhtml", "back").blocks.append(
                    block
                )
                continue
            if PurePosixPath(block.source_path).name == "ch2.xhtml" and (
                current is None or current.filename != "notes.xhtml"
            ):
                begin("Примечания", "notes.xhtml", "back")

        if profile == "generic":
            match = re.match(r"^(?:chapter|глава)\s+(\d+)[.:\s-]+(.+)$", text, re.IGNORECASE)
            if match:
                number = int(match.group(1))
                begin(text, "chapter-{:02d}.xhtml".format(number), "chapter").blocks.append(block)
                continue
        if current is not None:
            current.blocks.append(block)

    if current is not None and current.blocks:
        sections.append(current)
    return [section for section in sections if section.blocks]


def _bregg_chapter_number(normalized: str) -> Optional[int]:
    for index, title in enumerate(_BREGG_CHAPTERS, start=1):
        if normalized == title:
            return index
    return None


def _bregg_appendix(normalized: str) -> Optional[Tuple[str, str]]:
    normalized = normalized.replace("СПИСОК", "СПИСОК")
    for letter, title in _BREGG_APPENDICES:
        if normalized == title:
            return letter, title
    return None


def _drop_trailing_marker(section: Optional[_RebuiltSection], marker: str) -> None:
    if (
        section
        and section.blocks
        and _normalized_title(_element_text(section.blocks[-1].element)) == marker
    ):
        section.blocks.pop()


def _title_case_heading(text: str) -> str:
    words = text.lower().split()
    if not words:
        return text
    return "{}{}".format(words[0].capitalize(), " " + " ".join(words[1:]) if len(words) > 1 else "")


def _clean_section_text(
    sections: Sequence[_RebuiltSection],
    profile: str,
    morphology: Optional[pymorphy3.MorphAnalyzer],
) -> Dict[str, int]:
    counts = {"hyphens": 0, "ocr": 0}
    for section in sections:
        for block in section.blocks:
            for element in block.element.iter():
                if element.text:
                    element.text, changes = _clean_text(element.text, profile, morphology)
                    counts["hyphens"] += changes[0]
                    counts["ocr"] += changes[1]
                if element.tail:
                    element.tail, changes = _clean_text(element.tail, profile, morphology)
                    counts["hyphens"] += changes[0]
                    counts["ocr"] += changes[1]
    return counts


def _clean_text(
    text: str,
    profile: str,
    morphology: Optional[pymorphy3.MorphAnalyzer],
) -> Tuple[str, Tuple[int, int]]:
    hyphen_count = 0
    ocr_count = 0
    if morphology is not None:

        def replace_hyphenated(match: "re.Match[str]") -> str:
            nonlocal hyphen_count
            fixed, count = _repair_hyphenated_word(
                match.group(0),
                morphology,
                aggressive=profile == "zvezdin",
            )
            hyphen_count += count
            return fixed

        text = _HYPHENATED_WORD_PATTERN.sub(replace_hyphenated, text)
    if profile == "zvezdin":
        text = re.sub(
            r"\bбл\s+а\s+г\s+од\s+а\s+рн\s+о\s+с\s+т\s+и\b",
            "благодарности",
            text,
            flags=re.IGNORECASE,
        )

        def replace_zvezdin_word(match: "re.Match[str]") -> str:
            nonlocal ocr_count
            direct = _ZVEZDIN_WORD_FIXES.get(match.group(0).casefold())
            if not direct:
                return match.group(0)
            ocr_count += 1
            return _match_case(match.group(0), direct)

        text = _CYRILLIC_WORD_PATTERN.sub(replace_zvezdin_word, text)
    if profile == "bregg" and morphology is not None:

        def replace_word(match: "re.Match[str]") -> str:
            nonlocal ocr_count
            fixed = _repair_bregg_word(match.group(0), morphology)
            if fixed != match.group(0):
                ocr_count += 1
            return fixed

        text = _CYRILLIC_WORD_PATTERN.sub(replace_word, text)
    return text, (hyphen_count, ocr_count)


def _repair_hyphenated_word(
    token: str,
    morphology: pymorphy3.MorphAnalyzer,
    *,
    aggressive: bool,
) -> Tuple[str, int]:
    folded = token.casefold()
    direct = _ZVEZDIN_HYPHEN_FIXES.get(folded) if aggressive else _BREGG_HYPHEN_FIXES.get(folded)
    if direct:
        return _match_case(token, direct), 1
    if folded in _VALID_HYPHENATED_FOLDED or morphology.word_is_known(folded):
        return token, 0
    parts = token.split("-")
    changes = 0
    index = 0
    while index < len(parts) - 1:
        left, right = parts[index], parts[index + 1]
        joined = left + right
        if not aggressive and _preserve_hyphen(left, right, morphology):
            index += 1
            continue
        if not (left.isdigit() or right.isdigit()) and morphology.word_is_known(joined.casefold()):
            parts[index : index + 2] = [joined]
            changes += 1
            if index:
                index -= 1
            continue
        if aggressive and _preserve_hyphen(left, right, morphology):
            index += 1
            continue
        if (
            not aggressive
            and morphology.word_is_known(left.casefold())
            and morphology.word_is_known(right.casefold())
        ):
            index += 1
            continue
        parts[index : index + 2] = [joined]
        changes += 1
        if index:
            index -= 1
    result = "-".join(parts)
    if aggressive and (direct := _ZVEZDIN_HYPHEN_FIXES.get(result.casefold())):
        return _match_case(token, direct), changes + 1
    return result, changes


def _preserve_hyphen(
    left: str,
    right: str,
    morphology: pymorphy3.MorphAnalyzer,
) -> bool:
    if left.isdigit() or right.isdigit():
        return True
    left_folded, right_folded = left.casefold(), right.casefold()
    pair = "{}-{}".format(left_folded, right_folded)
    if pair in _VALID_HYPHENATED_FOLDED:
        return True
    if right_folded in _PARTICLE_RIGHT or any(
        left_folded.startswith(prefix) for prefix in _PREFIX_LEFT
    ):
        return True
    return left_folded == right_folded or bool(morphology.word_is_known(pair))


def _repair_bregg_word(
    word: str,
    morphology: pymorphy3.MorphAnalyzer,
) -> str:
    folded = word.casefold().replace("ё", "е")
    direct = _BREGG_WORD_FIXES.get(folded)
    if direct:
        return _match_case(word, direct)
    for source, target in _BREGG_SEQUENCE_FIXES:
        if source not in folded:
            continue
        candidate = folded.replace(source, target)
        if morphology.word_is_known(candidate):
            return _match_case(word, candidate)
    if "лс" not in folded:
        return word
    candidate = re.sub("лс", "ж", folded)
    if morphology.word_is_known(candidate):
        return _match_case(word, candidate)
    return word


def _match_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement.capitalize()
    return replacement


def _expand_run_in_headings(
    sections: Sequence[_RebuiltSection],
    profile: str,
) -> Dict[str, int]:
    counts = {"headings": 0, "paragraphs": 0}
    if profile != "bregg":
        return counts

    for section in sections:
        expanded: List[_SourceBlock] = []
        for block in section.blocks:
            replacements = _split_run_in_paragraph(block)
            if replacements is None:
                expanded.append(block)
                continue
            expanded.extend(replacements)
            counts["headings"] += sum(_local_name(item.element) == "h3" for item in replacements)
        section.blocks = expanded
        counts["paragraphs"] += _join_split_topic_paragraphs(section.blocks)
    return counts


def _split_run_in_paragraph(
    block: _SourceBlock,
) -> Optional[List[_SourceBlock]]:
    paragraph = block.element
    if _local_name(paragraph) != "p":
        return None
    tokens = _mixed_tokens(paragraph)
    first = _heading_at_paragraph_start(tokens)
    if first is None:
        return None
    heading, body_tokens = first
    replacements: List[_SourceBlock] = []
    original_id = paragraph.attrib.get("id")

    while True:
        later = _later_heading(body_tokens)
        if later is None:
            body_before, next_heading, remaining = body_tokens, None, []
        else:
            body_before, next_heading, remaining = later
        replacements.append(
            _SourceBlock(
                _topic_heading_element(heading, original_id),
                block.source_path,
            )
        )
        original_id = None
        body = _paragraph_from_tokens(paragraph, body_before)
        if _element_text(body):
            replacements.append(_SourceBlock(body, block.source_path))
        if next_heading is None:
            break
        heading = next_heading
        body_tokens = remaining
    return replacements


def _mixed_tokens(element: ET.Element) -> List[_MixedToken]:
    tokens: List[_MixedToken] = []
    if element.text:
        tokens.append(_MixedToken("text", element.text))
    for child in list(element):
        clone = copy.deepcopy(child)
        tail = clone.tail
        clone.tail = None
        tokens.append(_MixedToken("element", clone))
        if tail:
            tokens.append(_MixedToken("text", tail))
    return tokens


def _heading_at_paragraph_start(
    tokens: Sequence[_MixedToken],
) -> Optional[Tuple[str, List[_MixedToken]]]:
    meaningful = next(
        (token for token in tokens if token.kind == "element" or str(token.value).strip()),
        None,
    )
    if meaningful is None:
        return None
    starts_with_emphasis = (
        meaningful.kind == "element" and _local_name(meaningful.value) == "em"  # type: ignore[arg-type]
    )
    starts_with_marker = meaningful.kind == "text" and _starts_with_topic_marker(
        str(meaningful.value)
    )
    if not (starts_with_emphasis or starts_with_marker):
        return None

    heading_tokens: List[_MixedToken] = []
    body_tokens: List[_MixedToken] = []
    found_emphasis = False
    found_boundary = False
    for index, token in enumerate(tokens):
        token_text = _mixed_token_text(token)
        if token.kind == "element" and _local_name(token.value) == "em":  # type: ignore[arg-type]
            found_emphasis = True
        period = token_text.find(".")
        if period < 0:
            heading_tokens.append(token)
            continue
        if token.kind == "element":
            if token_text[period + 1 :].strip():
                return None
            heading_tokens.append(token)
        else:
            heading_part = token_text[: period + 1]
            remainder = token_text[period + 1 :]
            if heading_part:
                heading_tokens.append(_MixedToken("text", heading_part))
            if remainder:
                body_tokens.append(_MixedToken("text", remainder))
        body_tokens.extend(copy.deepcopy(list(tokens[index + 1 :])))
        found_boundary = True
        break

    raw_heading = _tokens_text(heading_tokens)
    heading = _clean_topic_heading(raw_heading)
    if (
        not found_boundary
        or not found_emphasis
        or not (3 <= len(heading) <= 140)
        or not _starts_with_uppercase(heading)
        or len(_tokens_text(body_tokens).strip()) < 10
    ):
        return None
    _trim_leading_text(body_tokens)
    return heading, body_tokens


def _later_heading(
    tokens: Sequence[_MixedToken],
) -> Optional[Tuple[List[_MixedToken], str, List[_MixedToken]]]:
    for index, token in enumerate(tokens):
        if token.kind != "element":
            continue
        element = token.value
        if not isinstance(element, ET.Element) or _local_name(element) != "em":
            continue
        heading = _clean_topic_heading(_element_text(element))
        if (
            not heading
            or not _element_text(element).rstrip().endswith(".")
            or not _starts_with_uppercase(heading)
            or not (3 <= len(heading) <= 140)
        ):
            continue
        before = copy.deepcopy(list(tokens[:index]))
        after = copy.deepcopy(list(tokens[index + 1 :]))
        before_text = _tokens_text(before).rstrip()
        if not before_text.endswith((".", "!", "?", "…")):
            continue
        _trim_trailing_text(before)
        _trim_leading_text(after)
        if len(_tokens_text(after).strip()) < 10:
            continue
        return before, heading, after
    return None


def _mixed_token_text(token: _MixedToken) -> str:
    if token.kind == "text":
        return str(token.value)
    value = token.value
    return _element_text(value) if isinstance(value, ET.Element) else ""


def _tokens_text(tokens: Sequence[_MixedToken]) -> str:
    return "".join(_mixed_token_text(token) for token in tokens)


def _starts_with_topic_marker(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return False
    if stripped[0] in "®©•·—–-.*":
        return True
    return bool(re.match(r"^[oо]\s+(?=[A-ZА-ЯЁ])", stripped))


def _clean_topic_heading(text: str) -> str:
    text = _SPACE_PATTERN.sub(" ", text).strip()
    text = re.sub(r"^[®©•·—–\-.*\s]+", "", text)
    text = re.sub(r"^[oо]\s+(?=[A-ZА-ЯЁ])", "", text)
    text = re.sub(r"^Р1зменения\b", "Изменения", text)
    return text.rstrip(".").strip()


def _starts_with_uppercase(text: str) -> bool:
    first_letter = next((character for character in text if character.isalpha()), "")
    return bool(first_letter and first_letter.isupper())


def _trim_leading_text(tokens: List[_MixedToken]) -> None:
    while tokens and tokens[0].kind == "text":
        value = str(tokens[0].value).lstrip()
        if value:
            tokens[0].value = value
            return
        tokens.pop(0)


def _trim_trailing_text(tokens: List[_MixedToken]) -> None:
    while tokens and tokens[-1].kind == "text":
        value = str(tokens[-1].value).rstrip()
        if value:
            tokens[-1].value = value
            return
        tokens.pop()


def _topic_heading_element(title: str, element_id: Optional[str]) -> ET.Element:
    attributes = {"class": "topic-heading"}
    if element_id:
        attributes["id"] = element_id
    heading = ET.Element(_qualified(_XHTML_NS, "h3"), attributes)
    heading.text = title
    return heading


def _paragraph_from_tokens(
    template: ET.Element,
    tokens: Sequence[_MixedToken],
) -> ET.Element:
    attributes = dict(template.attrib)
    attributes.pop("id", None)
    classes = attributes.get("class", "").split()
    if "topic-body" not in classes:
        classes.append("topic-body")
    attributes["class"] = " ".join(classes).strip()
    paragraph = ET.Element(_qualified(_XHTML_NS, "p"), attributes)
    for token in tokens:
        if token.kind == "text":
            _append_text(paragraph, str(token.value), separator="")
        elif isinstance(token.value, ET.Element):
            paragraph.append(copy.deepcopy(token.value))
    return paragraph


def _append_text(element: ET.Element, text: str, *, separator: str) -> None:
    if not text:
        return
    if len(element):
        last = element[-1]
        last.tail = "{}{}{}".format(last.tail or "", separator, text)
    else:
        element.text = "{}{}{}".format(element.text or "", separator, text)


def _join_split_topic_paragraphs(blocks: List[_SourceBlock]) -> int:
    joins = 0
    index = 0
    while index + 1 < len(blocks):
        current = blocks[index].element
        following = blocks[index + 1].element
        current_classes = current.attrib.get("class", "").split()
        current_text = _element_text(current)
        following_text = _element_text(following)
        if (
            _local_name(current) == "p"
            and "topic-body" in current_classes
            and _local_name(following) == "p"
            and current_text
            and current_text[-1] not in ".!?…:;»)"
            and following_text[:1].islower()
        ):
            _append_paragraph_content(current, following)
            blocks.pop(index + 1)
            joins += 1
            continue
        index += 1
    return joins


_BREGG_BULLET_GLYPHS = "•·‧∙⋅●○◦▪■□◆◇▶►⁃⁌⁍‣®©»*"
_BREGG_BULLET_PREFIX = re.compile(
    r"^[\s\u00a0]*(?P<marker>"
    r"[{}]+|[oо](?=[\s\u00a0]+[A-ZА-ЯЁ])|\.(?=[\s\u00a0]*[A-ZА-ЯЁ])"
    r")[\s\u00a0]*".format(re.escape(_BREGG_BULLET_GLYPHS))
)


def _reconstruct_bullet_lists(
    sections: Sequence[_RebuiltSection],
    profile: str,
) -> Dict[str, int]:
    counts = {"items": 0, "lists": 0}
    if profile != "bregg":
        return counts

    for section in sections:
        rebuilt: List[_SourceBlock] = []
        current_list: Optional[_SourceBlock] = None
        for block in section.blocks:
            item = _list_item_from_ocr_marker(block.element)
            if item is None:
                rebuilt.append(block)
                current_list = None
                continue

            if current_list is None or current_list.source_path != block.source_path:
                unordered = ET.Element(
                    _qualified(_XHTML_NS, "ul"),
                    {"class": "normalized-list"},
                )
                current_list = _SourceBlock(unordered, block.source_path)
                rebuilt.append(current_list)
                counts["lists"] += 1
            current_list.element.append(item)
            counts["items"] += 1
        section.blocks = rebuilt
    return counts


def _list_item_from_ocr_marker(paragraph: ET.Element) -> Optional[ET.Element]:
    if _local_name(paragraph) != "p":
        return None
    if _BREGG_BULLET_PREFIX.match(_element_text(paragraph)) is None:
        return None

    item = copy.deepcopy(paragraph)
    if not _strip_leading_bullet_marker(item):
        return None
    _retag(item, "li")
    classes = [
        name
        for name in item.attrib.get("class", "").split()
        if name not in {"p", "paragraph", "topic-body"}
    ]
    if classes:
        item.set("class", " ".join(classes))
    else:
        item.attrib.pop("class", None)
    return item


def _strip_leading_bullet_marker(element: ET.Element) -> bool:
    for owner, attribute in _text_slots(element):
        value = getattr(owner, attribute) or ""
        if not value.strip():
            continue
        match = _BREGG_BULLET_PREFIX.match(value)
        if match is None:
            return False
        setattr(owner, attribute, value[match.end() :])
        return True
    return False


def _text_slots(element: ET.Element) -> Iterable[Tuple[ET.Element, str]]:
    yield element, "text"
    for child in list(element):
        yield from _text_slots(child)
        yield child, "tail"


def _append_paragraph_content(target: ET.Element, source: ET.Element) -> None:
    source_text = source.text or ""
    target_text = _element_text(target)
    separator = " "
    if target_text.endswith("-"):
        _remove_final_text_character(target, "-")
        separator = ""
    _append_text(target, source_text.lstrip(), separator=separator)
    for child in list(source):
        target.append(copy.deepcopy(child))


def _remove_final_text_character(element: ET.Element, character: str) -> None:
    if len(element) and (element[-1].tail or "").rstrip().endswith(character):
        tail = element[-1].tail or ""
        position = tail.rfind(character)
        element[-1].tail = tail[:position] + tail[position + 1 :]
    elif (element.text or "").rstrip().endswith(character):
        text = element.text or ""
        position = text.rfind(character)
        element.text = text[:position] + text[position + 1 :]


def _promote_headings(sections: Sequence[_RebuiltSection], profile: str) -> None:
    for section in sections:
        for index, block in enumerate(section.blocks):
            element = block.element
            text = _element_text(element)
            is_first = index == 0
            if is_first and section.filename not in {
                "frontmatter.xhtml",
                "notes.xhtml",
            }:
                _retag(element, "h1")
                element.attrib.pop("class", None)
                _replace_element_text(element, section.title)
                continue
            if profile == "bregg" and element.attrib.get("class") == "subtitle":
                if _normalized_title(text) not in {"ГЛАВА", "ПРИЛОЖЕНИЕ"}:
                    _retag(element, "h2")
                    element.attrib.pop("class", None)
            elif profile == "zvezdin" and _is_zvezdin_subheading(element, text):
                _retag(element, "h2")
                element.attrib.pop("class", None)


def _is_zvezdin_subheading(element: ET.Element, text: str) -> bool:
    if not (3 <= len(text) <= 110) or text[-1:] in {".", ",", ";", ":"}:
        return False
    if text.startswith(("-", "–", "—", "«")):
        return False
    if text.isupper() and " " not in text:
        return False
    children = list(element)
    return bool(children) and all(_local_name(child) in {"strong", "b"} for child in children)


def _retag(element: ET.Element, local_name: str) -> None:
    element.tag = _qualified(_XHTML_NS, local_name)


def _replace_element_text(element: ET.Element, text: str) -> None:
    for child in list(element):
        element.remove(child)
    element.text = text


def _describe_images(sections: Sequence[_RebuiltSection], profile: str) -> int:
    described = 0
    figure_number = 0
    for section in sections:
        previous_text = ""
        for block in section.blocks:
            text = _element_text(block.element)
            for image in [
                element for element in block.element.iter() if _local_name(element) == "img"
            ]:
                if profile != "zvezdin" and image.attrib.get("alt", "").strip():
                    continue
                source_name = PurePosixPath(image.attrib.get("src", "")).name
                source_match = re.fullmatch(
                    r"_([0-9]+)\.jpe?g",
                    source_name,
                    re.IGNORECASE,
                )
                source_index = int(source_match.group(1)) if source_match else -1
                override = _FIGURE_ALT_OVERRIDES.get(source_index)
                if override:
                    alt = override
                else:
                    context = _figure_context(previous_text, section.title)
                    alt = "Иллюстрация {} к разделу «{}». {}".format(
                        figure_number + 1,
                        section.title,
                        context,
                    )
                image.set("alt", alt[:300])
                described += 1
                figure_number += 1
            if text:
                previous_text = text
    return described


def _figure_context(previous: str, fallback: str) -> str:
    previous = _SPACE_PATTERN.sub(" ", previous).strip()
    sentences = re.split(r"(?<=[.!?])\s+", previous)
    candidate = sentences[-1] if sentences else previous
    if 15 <= len(candidate) <= 170:
        return candidate
    return "Схема или график, поясняющий материал раздела {}.".format(fallback)


def _rewrite_internal_links(
    sections: Sequence[_RebuiltSection],
    old_content_paths: Sequence[str],
) -> None:
    id_targets: Dict[Tuple[str, str], str] = {}
    first_target: Dict[str, str] = {}
    package_directory = posixpath.dirname(old_content_paths[0])
    for section in sections:
        target = posixpath.join(package_directory, section.filename)
        for block in section.blocks:
            first_target.setdefault(block.source_path, target)
            for element in block.element.iter():
                element_id = element.attrib.get("id")
                if element_id:
                    id_targets[(block.source_path, element_id)] = target

    old_paths = set(old_content_paths)
    for section in sections:
        new_path = posixpath.join(package_directory, section.filename)
        for block in section.blocks:
            old_directory = posixpath.dirname(block.source_path)
            for anchor in [
                element for element in block.element.iter() if _local_name(element) == "a"
            ]:
                href = anchor.attrib.get("href")
                if not href:
                    continue
                parts = urlsplit(href)
                if parts.scheme or parts.netloc:
                    continue
                old_target = (
                    block.source_path
                    if not parts.path
                    else posixpath.normpath(posixpath.join(old_directory, unquote(parts.path)))
                )
                if old_target not in old_paths:
                    continue
                target = (
                    id_targets.get((old_target, parts.fragment))
                    if parts.fragment
                    else first_target.get(old_target)
                )
                if not target:
                    continue
                relative = posixpath.relpath(target, posixpath.dirname(new_path))
                anchor.set(
                    "href",
                    "{}{}".format(relative, "#{}".format(parts.fragment) if parts.fragment else ""),
                )


def _document_stylesheets(
    documents: Mapping[str, ET.Element],
) -> Tuple[ET.Element, ...]:
    first = next(iter(documents.values()))
    links = [
        copy.deepcopy(element)
        for element in first.iter()
        if _local_name(element) == "link" and element.attrib.get("rel") == "stylesheet"
    ]
    seen = set()
    unique = []
    for link in links:
        href = link.attrib.get("href")
        if href and href not in seen:
            unique.append(link)
            seen.add(href)
    unique.append(
        ET.Element(
            _qualified(_XHTML_NS, "link"),
            {"rel": "stylesheet", "type": "text/css", "href": "rebuild.css"},
        )
    )
    return tuple(unique)


def _contents_section(sections: Sequence[_RebuiltSection]) -> _RebuiltSection:
    heading = ET.Element(_qualified(_XHTML_NS, "h1"))
    heading.text = "Оглавление"
    listing = ET.Element(_qualified(_XHTML_NS, "ol"), {"class": "generated-toc"})
    for section in sections:
        if section.kind == "front" and section.filename == "frontmatter.xhtml":
            continue
        item = ET.SubElement(listing, _qualified(_XHTML_NS, "li"))
        anchor = ET.SubElement(
            item,
            _qualified(_XHTML_NS, "a"),
            {"href": section.filename},
        )
        anchor.text = section.title
    return _RebuiltSection(
        "Оглавление",
        "contents.xhtml",
        [
            _SourceBlock(heading, ""),
            _SourceBlock(listing, ""),
        ],
        "toc",
    )


def _serialize_xhtml(
    section: _RebuiltSection,
    language: str,
    stylesheets: Sequence[ET.Element],
) -> bytes:
    ET.register_namespace("", _XHTML_NS)
    ET.register_namespace("epub", _OPS_NS)
    html = ET.Element(
        _qualified(_XHTML_NS, "html"),
        {
            "lang": language,
            _qualified(_XML_NS, "lang"): language,
        },
    )
    head = ET.SubElement(html, _qualified(_XHTML_NS, "head"))
    title = ET.SubElement(head, _qualified(_XHTML_NS, "title"))
    title.text = section.title
    for stylesheet in stylesheets:
        head.append(copy.deepcopy(stylesheet))
    body = ET.SubElement(
        html,
        _qualified(_XHTML_NS, "body"),
        {_qualified(_OPS_NS, "type"): _epub_type(section.kind)},
    )
    if section.filename in {"frontmatter.xhtml", "notes.xhtml"}:
        heading = ET.SubElement(body, _qualified(_XHTML_NS, "h1"))
        heading.text = section.title
    for block in section.blocks:
        body.append(block.element)
    return ET.tostring(
        html,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )


def _epub_type(kind: str) -> str:
    return {
        "front": "frontmatter",
        "toc": "toc",
        "chapter": "chapter",
        "appendix": "appendix",
        "back": "backmatter",
    }.get(kind, "bodymatter")


def _navigation_xhtml(
    sections: Sequence[_RebuiltSection],
    metadata: _Metadata,
) -> bytes:
    ET.register_namespace("", _XHTML_NS)
    ET.register_namespace("epub", _OPS_NS)
    html = ET.Element(
        _qualified(_XHTML_NS, "html"),
        {
            "lang": metadata.language,
            _qualified(_XML_NS, "lang"): metadata.language,
        },
    )
    head = ET.SubElement(html, _qualified(_XHTML_NS, "head"))
    ET.SubElement(head, _qualified(_XHTML_NS, "title")).text = "Навигация"
    body = ET.SubElement(html, _qualified(_XHTML_NS, "body"))
    nav = ET.SubElement(
        body,
        _qualified(_XHTML_NS, "nav"),
        {_qualified(_OPS_NS, "type"): "toc", "id": "toc"},
    )
    ET.SubElement(nav, _qualified(_XHTML_NS, "h1")).text = "Оглавление"
    listing = ET.SubElement(nav, _qualified(_XHTML_NS, "ol"))
    for section in sections:
        item = ET.SubElement(listing, _qualified(_XHTML_NS, "li"))
        anchor = ET.SubElement(
            item,
            _qualified(_XHTML_NS, "a"),
            {"href": section.filename},
        )
        anchor.text = section.title
    landmarks = ET.SubElement(
        body,
        _qualified(_XHTML_NS, "nav"),
        {_qualified(_OPS_NS, "type"): "landmarks", "aria-label": "Ориентиры"},
    )
    landmark_list = ET.SubElement(landmarks, _qualified(_XHTML_NS, "ol"))
    for section in sections:
        if section.kind not in {"front", "toc", "chapter"}:
            continue
        item = ET.SubElement(landmark_list, _qualified(_XHTML_NS, "li"))
        link_type = (
            "toc"
            if section.kind == "toc"
            else ("bodymatter" if section.kind == "chapter" else "frontmatter")
        )
        anchor = ET.SubElement(
            item,
            _qualified(_XHTML_NS, "a"),
            {"href": section.filename, _qualified(_OPS_NS, "type"): link_type},
        )
        anchor.text = section.title
        if section.kind == "chapter":
            break
    return ET.tostring(html, encoding="utf-8", xml_declaration=True)


def _navigation_ncx(
    sections: Sequence[_RebuiltSection],
    metadata: _Metadata,
) -> bytes:
    ET.register_namespace("", _NCX_NS)
    ncx = ET.Element(_qualified(_NCX_NS, "ncx"), {"version": "2005-1"})
    head = ET.SubElement(ncx, _qualified(_NCX_NS, "head"))
    for name, content in (
        ("dtb:uid", metadata.identifier),
        ("dtb:depth", "1"),
        ("dtb:totalPageCount", "0"),
        ("dtb:maxPageNumber", "0"),
    ):
        ET.SubElement(head, _qualified(_NCX_NS, "meta"), {"name": name, "content": content})
    doc_title = ET.SubElement(ncx, _qualified(_NCX_NS, "docTitle"))
    ET.SubElement(doc_title, _qualified(_NCX_NS, "text")).text = metadata.title
    nav_map = ET.SubElement(ncx, _qualified(_NCX_NS, "navMap"))
    for order, section in enumerate(sections, start=1):
        point = ET.SubElement(
            nav_map,
            _qualified(_NCX_NS, "navPoint"),
            {"id": "nav-{}".format(order), "playOrder": str(order)},
        )
        label = ET.SubElement(point, _qualified(_NCX_NS, "navLabel"))
        ET.SubElement(label, _qualified(_NCX_NS, "text")).text = section.title
        ET.SubElement(
            point,
            _qualified(_NCX_NS, "content"),
            {"src": section.filename},
        )
    return ET.tostring(ncx, encoding="utf-8", xml_declaration=True)


def _existing_ncx_path(package: ET.Element, package_path: str) -> Optional[str]:
    directory = posixpath.dirname(package_path)
    for item in package.iter():
        if (
            _local_name(item) == "item"
            and item.attrib.get("media-type") == "application/x-dtbncx+xml"
            and item.attrib.get("href")
        ):
            return posixpath.normpath(posixpath.join(directory, item.attrib["href"]))
    return None


def _rebuild_package(
    package: ET.Element,
    package_path: str,
    metadata: _Metadata,
    sections: Sequence[_RebuiltSection],
    nav_path: str,
    ncx_path: str,
    style_path: str,
) -> bytes:
    ET.register_namespace("", _OPF_NS)
    ET.register_namespace("dc", _DC_NS)
    package.tag = _qualified(_OPF_NS, "package")
    package.set("version", "3.0")
    package.set("unique-identifier", _identifier_id(package))
    metadata_element = _direct_child(package, "metadata")
    manifest = _direct_child(package, "manifest")
    spine = _direct_child(package, "spine")
    if metadata_element is None or manifest is None or spine is None:
        raise EpubRepairError("EPUB package is missing metadata, manifest, or spine.")

    _set_dc_value(metadata_element, "title", metadata.title)
    _set_dc_value(metadata_element, "language", metadata.language)
    if metadata.author:
        _set_dc_value(metadata_element, "creator", metadata.author)
    elif (creator := _first_child(metadata_element, "creator")) is not None:
        metadata_element.remove(creator)
    for child in list(metadata_element):
        if isinstance(child.tag, str) and child.tag.startswith("{{{}}}".format(_DC_NS)):
            for attribute in list(child.attrib):
                if attribute not in {"id", "dir"} and attribute != _qualified(_XML_NS, "lang"):
                    child.attrib.pop(attribute)
        if _local_name(child) == "subject" and (child.text or "").strip().casefold() == "antique":
            metadata_element.remove(child)
        if _local_name(child) == "meta" and child.attrib.get("property") == "dcterms:modified":
            metadata_element.remove(child)
    modified = ET.SubElement(
        metadata_element,
        _qualified(_OPF_NS, "meta"),
        {"property": "dcterms:modified"},
    )
    modified.text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    directory = posixpath.dirname(package_path)
    for child in list(manifest):
        media_type = child.attrib.get("media-type")
        if media_type in {"application/xhtml+xml", "application/x-dtbncx+xml"}:
            manifest.remove(child)
        elif media_type in {"application/vnd.ms-opentype", "application/x-font-otf"}:
            child.set("media-type", "font/otf")
    used_ids = {child.attrib.get("id", "") for child in manifest}
    for index, section in enumerate(sections, start=1):
        ET.SubElement(
            manifest,
            _qualified(_OPF_NS, "item"),
            {
                "id": _available_id("section-{}".format(index), used_ids),
                "href": section.filename,
                "media-type": "application/xhtml+xml",
            },
        )
    ET.SubElement(
        manifest,
        _qualified(_OPF_NS, "item"),
        {
            "id": _available_id("nav", used_ids),
            "href": posixpath.relpath(nav_path, directory),
            "media-type": "application/xhtml+xml",
            "properties": "nav",
        },
    )
    ncx_id = _available_id("ncx", used_ids)
    ET.SubElement(
        manifest,
        _qualified(_OPF_NS, "item"),
        {
            "id": ncx_id,
            "href": posixpath.relpath(ncx_path, directory),
            "media-type": "application/x-dtbncx+xml",
        },
    )
    ET.SubElement(
        manifest,
        _qualified(_OPF_NS, "item"),
        {
            "id": _available_id("rebuild-css", used_ids),
            "href": posixpath.relpath(style_path, directory),
            "media-type": "text/css",
        },
    )

    for child in list(spine):
        spine.remove(child)
    spine.set("toc", ncx_id)
    section_items = [
        child for child in manifest if child.attrib.get("id", "").startswith("section-")
    ]
    for item in section_items:
        ET.SubElement(
            spine,
            _qualified(_OPF_NS, "itemref"),
            {"idref": item.attrib["id"]},
        )
    for child in list(package):
        if _local_name(child) == "guide":
            package.remove(child)
    return ET.tostring(package, encoding="utf-8", xml_declaration=True)


def _identifier_id(package: ET.Element) -> str:
    unique = package.attrib.get("unique-identifier")
    if unique:
        for element in package.iter():
            if _local_name(element) == "identifier" and element.attrib.get("id") == unique:
                return unique
    for element in package.iter():
        if _local_name(element) == "identifier":
            element.set("id", "bookid")
            return "bookid"
    metadata = _direct_child(package, "metadata")
    if metadata is None:
        return "bookid"
    identifier = ET.SubElement(
        metadata,
        _qualified(_DC_NS, "identifier"),
        {"id": "bookid"},
    )
    identifier.text = "urn:uuid:repaired-publication"
    return "bookid"


def _set_dc_value(parent: ET.Element, name: str, value: str) -> None:
    element = _first_child(parent, name)
    if element is None:
        element = ET.SubElement(parent, _qualified(_DC_NS, name))
    element.text = value
    for duplicate in [
        child for child in list(parent) if _local_name(child) == name and child is not element
    ]:
        parent.remove(duplicate)


def _direct_child(parent: ET.Element, name: str) -> Optional[ET.Element]:
    return next((child for child in parent if _local_name(child) == name), None)


def _first_child(parent: ET.Element, name: str) -> Optional[ET.Element]:
    return next((child for child in parent.iter() if _local_name(child) == name), None)


def _available_id(desired: str, used: MutableSet[str]) -> str:
    candidate = desired
    counter = 1
    while candidate in used:
        counter += 1
        candidate = "{}-{}".format(desired, counter)
    used.add(candidate)
    return candidate


def _rebuild_css() -> bytes:
    return (
        "h1 { margin: 1.5em 0 1em; text-align: center; }\n"
        "h2 { margin: 1.4em 0 0.6em; page-break-after: avoid; }\n"
        "h3.topic-heading { font-size: 1em; font-style: italic; font-weight: 600; "
        "margin: 1em 0 0.2em; page-break-after: avoid; text-align: left; }\n"
        "p.topic-body { margin-top: 0; }\n"
        "ul.normalized-list { margin: 0.5em 0 1em 1.5em; padding-left: 1.2em; "
        "list-style-type: disc; }\n"
        "ul.normalized-list li { margin: 0.25em 0; }\n"
        "img { max-width: 100%; height: auto; }\n"
        "table { max-width: 100%; }\n"
        ".generated-toc { line-height: 1.45; }\n"
        ".generated-toc li { margin: 0.35em 0; }\n"
    ).encode("utf-8")
