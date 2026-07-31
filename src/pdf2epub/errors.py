"""Focused errors raised by the public conversion and repair APIs."""


class Pdf2EpubError(Exception):
    """Base class for expected conversion failures."""


class InputFileError(Pdf2EpubError):
    """The input, output, or an option is invalid."""


class PdfReadError(Pdf2EpubError):
    """The PDF cannot be parsed or is unsupported."""


class NoExtractableTextError(PdfReadError):
    """The PDF contains no text that pypdf can extract."""


class EpubWriteError(Pdf2EpubError):
    """The EPUB could not be assembled or written."""


class EpubReadError(Pdf2EpubError):
    """The source EPUB archive or package cannot be read safely."""


class EpubRepairError(Pdf2EpubError):
    """The source EPUB contains defects that cannot be repaired safely."""


class EpubValidationError(EpubRepairError):
    """EPUBCheck is unavailable or the repaired EPUB still has errors."""
