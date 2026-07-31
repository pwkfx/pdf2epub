"""Lazy Tesseract OCR integration shared by PDF and DjVu conversion."""

import csv
import io
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

from .errors import MissingDependencyError, OcrError
from .models import TextLine

_OCR_LANGUAGE_MAP = {
    "en": "eng",
    "ru": "rus",
    "uk": "ukr",
    "de": "deu",
    "fr": "fra",
    "es": "spa",
    "it": "ita",
    "pt": "por",
    "nl": "nld",
    "pl": "pol",
    "cs": "ces",
    "ja": "jpn",
    "ko": "kor",
    "ar": "ara",
    "zh-hans": "chi_sim",
    "zh-hant": "chi_tra",
}
_OCR_LANGUAGE = re.compile(r"^[A-Za-z0-9_-]+(?:\+[A-Za-z0-9_-]+)*$")
_MINIMUM_WORD_CONFIDENCE = 30.0


@dataclass(frozen=True)
class OcrWord:
    """A recognized word and its top-left raster coordinates."""

    text: str
    left: int
    top: int
    width: int
    height: int
    block: int
    paragraph: int
    line: int
    confidence: float


@dataclass(frozen=True)
class OcrPage:
    """Recognized words plus lines scaled to the target document canvas."""

    words: Tuple[OcrWord, ...]
    lines: Tuple[TextLine, ...]
    language: str


def recognize_image(
    image: object,
    page_number: int,
    target_width: float,
    target_height: float,
    *,
    requested_language: Optional[str],
    publication_language: Optional[str],
    warnings: List[str],
) -> OcrPage:
    """Run Tesseract TSV OCR and reconstruct layout-aware text lines."""

    command = _find_tesseract()
    language = _resolve_ocr_language(
        command,
        requested_language=requested_language,
        publication_language=publication_language,
        warnings=warnings,
    )
    words = tuple(
        word
        for word in _run_tesseract(command, image, language)
        if word.confidence >= _MINIMUM_WORD_CONFIDENCE
        and any(character.isalnum() for character in word.text)
    )
    lines = _words_to_lines(
        words,
        page_number,
        float(image.width),
        float(image.height),
        target_width,
        target_height,
    )
    return OcrPage(words, tuple(lines), language)


def _find_tesseract() -> str:
    configured = os.environ.get("TESSERACT_CMD")
    candidates = [
        configured,
        shutil.which("tesseract"),
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise MissingDependencyError(
        "Tesseract OCR is required for image-only pages. Install 'tesseract-ocr' "
        "and its language data, or set TESSERACT_CMD."
    )


def _resolve_ocr_language(
    command: str,
    *,
    requested_language: Optional[str],
    publication_language: Optional[str],
    warnings: List[str],
) -> str:
    explicit = requested_language is not None
    if explicit:
        language = str(requested_language).strip()
        if not language or not _OCR_LANGUAGE.fullmatch(language):
            raise OcrError(
                "OCR language must use Tesseract codes such as 'eng', 'rus', or 'eng+rus'."
            )
    else:
        normalized = str(publication_language or "").strip().casefold()
        language = _OCR_LANGUAGE_MAP.get(normalized, "")
        if not language and "-" in normalized:
            language = _OCR_LANGUAGE_MAP.get(normalized.split("-", 1)[0], "")
        if not language:
            language = "eng"
            if normalized and normalized != "und":
                warnings.append(
                    "No Tesseract language mapping is known for {!r}; using English OCR.".format(
                        publication_language
                    )
                )

    available = _available_languages(command)
    missing = [part for part in language.split("+") if part not in available]
    if not missing:
        return language
    if explicit:
        raise OcrError(
            "Tesseract language data is not installed for: {}.".format(", ".join(missing))
        )
    if "eng" in available:
        warnings.append(
            "Tesseract language data for {} is unavailable; using English OCR.".format(
                ", ".join(missing)
            )
        )
        return "eng"
    raise OcrError(
        "Tesseract language data is unavailable for {} and English is not installed.".format(
            ", ".join(missing)
        )
    )


def _available_languages(command: str) -> Sequence[str]:
    try:
        completed = subprocess.run(
            [command, "--list-langs"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OcrError("Unable to query Tesseract language data: {}".format(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise OcrError("Unable to query Tesseract language data: {}".format(detail))
    return {
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip() and not line.casefold().startswith("list of available languages")
    }


def _run_tesseract(command: str, image: object, language: str) -> List[OcrWord]:
    descriptor, temporary_name = tempfile.mkstemp(prefix="pdf2epub-ocr-", suffix=".png")
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        image.save(str(temporary_path), format="PNG")
        completed = subprocess.run(
            [
                command,
                str(temporary_path),
                "stdout",
                "-l",
                language,
                "--psm",
                "3",
                "tsv",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError("Tesseract timed out while recognizing a page.") from exc
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise OcrError("Unable to run Tesseract: {}".format(exc)) from exc
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise OcrError("Tesseract failed: {}".format(detail or "unknown error"))
    return _parse_tsv(completed.stdout)


def _parse_tsv(content: str) -> List[OcrWord]:
    words: List[OcrWord] = []
    reader = csv.DictReader(io.StringIO(content), delimiter="\t")
    required = {
        "level",
        "block_num",
        "par_num",
        "line_num",
        "left",
        "top",
        "width",
        "height",
        "conf",
        "text",
    }
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        if content.strip():
            raise OcrError("Tesseract returned malformed TSV output.")
        return words
    for row in reader:
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        try:
            words.append(
                OcrWord(
                    text=text,
                    left=int(row["left"]),
                    top=int(row["top"]),
                    width=max(1, int(row["width"])),
                    height=max(1, int(row["height"])),
                    block=int(row["block_num"]),
                    paragraph=int(row["par_num"]),
                    line=int(row["line_num"]),
                    confidence=float(row["conf"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OcrError("Tesseract returned malformed word coordinates.") from exc
    return words


def _words_to_lines(
    words: Sequence[OcrWord],
    page_number: int,
    image_width: float,
    image_height: float,
    target_width: float,
    target_height: float,
) -> List[TextLine]:
    groups: Dict[Tuple[int, int, int], List[OcrWord]] = {}
    order: List[Tuple[int, int, int]] = []
    for word in words:
        key = (word.block, word.paragraph, word.line)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(word)

    x_scale = target_width / image_width
    y_scale = target_height / image_height
    assembled = []
    for key in order:
        line_words = sorted(groups[key], key=lambda item: item.left)
        left = min(item.left for item in line_words)
        right = max(item.left + item.width for item in line_words)
        top = min(item.top for item in line_words)
        bottom = max(item.top + item.height for item in line_words)
        assembled.append(
            (
                key,
                " ".join(item.text for item in line_words),
                left * x_scale,
                target_height - bottom * y_scale,
                max(1.0, median(item.height for item in line_words) * y_scale),
                (right - left) * x_scale,
                top * y_scale,
                bottom * y_scale,
            )
        )

    lines: List[TextLine] = []
    for index, item in enumerate(assembled):
        key, text, x, y, size, width, top, bottom = item
        previous = assembled[index - 1] if index else None
        following = assembled[index + 1] if index + 1 < len(assembled) else None
        gap_before = top - previous[7] if previous is not None else None
        gap_after = following[6] - bottom if following is not None else None
        paragraph_start = previous is None or previous[0][:2] != key[:2]
        paragraph_end = following is None or following[0][:2] != key[:2]
        centered = abs((x + width / 2.0) - target_width / 2.0) <= target_width * 0.12
        lines.append(
            TextLine(
                text=text,
                page_number=page_number,
                x=x,
                y=y,
                font_size=size,
                centered=centered,
                gap_before=gap_before,
                blank_before=paragraph_start
                or bool(gap_before is not None and gap_before > size * 1.6),
                blank_after=paragraph_end or bool(gap_after is not None and gap_after > size * 1.6),
                page_start=index == 0,
                page_end=index + 1 == len(assembled),
            )
        )
    return lines
