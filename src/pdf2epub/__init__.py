"""Public interface for document conversion and EPUB repair."""

from .api import convert_document, convert_pdf, repair_epub
from .errors import (
    DjvuReadError,
    DocumentReadError,
    EpubReadError,
    EpubRepairError,
    EpubValidationError,
    EpubWriteError,
    InputFileError,
    MissingDependencyError,
    NoExtractableTextError,
    OcrError,
    Pdf2EpubError,
    PdfReadError,
)
from .models import ConversionOptions, ConversionResult, RepairOptions, RepairResult

__all__ = [
    "ConversionOptions",
    "ConversionResult",
    "DjvuReadError",
    "DocumentReadError",
    "EpubReadError",
    "EpubRepairError",
    "EpubValidationError",
    "EpubWriteError",
    "InputFileError",
    "MissingDependencyError",
    "NoExtractableTextError",
    "OcrError",
    "Pdf2EpubError",
    "PdfReadError",
    "RepairOptions",
    "RepairResult",
    "convert_document",
    "convert_pdf",
    "repair_epub",
]

__version__ = "0.5.0"
