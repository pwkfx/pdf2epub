"""Public interface for the PDF-to-EPUB converter."""

from .api import convert_pdf
from .errors import (
    EpubWriteError,
    InputFileError,
    NoExtractableTextError,
    Pdf2EpubError,
    PdfReadError,
)
from .models import ConversionOptions, ConversionResult

__all__ = [
    "ConversionOptions",
    "ConversionResult",
    "EpubWriteError",
    "InputFileError",
    "NoExtractableTextError",
    "Pdf2EpubError",
    "PdfReadError",
    "convert_pdf",
]

__version__ = "0.2.0"
