"""Public interface for PDF conversion and EPUB repair."""

from .api import convert_pdf, repair_epub
from .errors import (
    EpubReadError,
    EpubRepairError,
    EpubValidationError,
    EpubWriteError,
    InputFileError,
    NoExtractableTextError,
    Pdf2EpubError,
    PdfReadError,
)
from .models import ConversionOptions, ConversionResult, RepairOptions, RepairResult

__all__ = [
    "ConversionOptions",
    "ConversionResult",
    "EpubReadError",
    "EpubRepairError",
    "EpubValidationError",
    "EpubWriteError",
    "InputFileError",
    "NoExtractableTextError",
    "Pdf2EpubError",
    "PdfReadError",
    "RepairOptions",
    "RepairResult",
    "convert_pdf",
    "repair_epub",
]

__version__ = "0.4.0"
