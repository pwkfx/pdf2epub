"""Public result types and private pipeline models."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class ConversionOptions:
    """Optional metadata overrides and output behavior."""

    title: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    overwrite: bool = False


@dataclass(frozen=True)
class ConversionResult:
    """Summary of a completed conversion."""

    output_path: Path
    title: str
    author: Optional[str]
    language: str
    identifier: str
    chapter_count: int
    warnings: Tuple[str, ...]


@dataclass(frozen=True)
class PublicationMetadata:
    title: str
    author: Optional[str]
    language: str
    identifier: str


@dataclass(frozen=True)
class TextLine:
    text: str
    page_number: int
    x: Optional[float] = None
    y: Optional[float] = None
    font_size: Optional[float] = None
    font_name: str = ""
    bold: bool = False
    italic: bool = False
    centered: bool = False
    gap_before: Optional[float] = None
    blank_before: bool = False
    blank_after: bool = False
    page_start: bool = False
    page_end: bool = False

    @property
    def has_layout(self) -> bool:
        return self.x is not None and self.y is not None and self.font_size is not None


@dataclass(frozen=True)
class ExtractedPage:
    number: int
    width: float
    height: float
    lines: Tuple[TextLine, ...]


@dataclass(frozen=True)
class ExtractedDocument:
    pages: Tuple[ExtractedPage, ...]
    title: Optional[str]
    author: Optional[str]
    language: Optional[str]
    outline_titles: Tuple[str, ...]
    warnings: Tuple[str, ...]


@dataclass(frozen=True)
class Block:
    kind: str
    text: str


@dataclass(frozen=True)
class Section:
    filename: str
    title: str
    blocks: Tuple[Block, ...]
    navigation_id: str
    is_preface: bool = False
