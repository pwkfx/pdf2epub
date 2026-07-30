"""Focused errors raised by the public conversion API."""


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
