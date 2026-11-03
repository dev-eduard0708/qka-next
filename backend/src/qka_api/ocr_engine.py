"""Local OCR via Tesseract. Never invents text when the engine is unavailable."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from qka_shared.config import Settings

log = logging.getLogger("qka.ocr")

_DEFAULT_TESSERACT_CANDIDATES = (
    r"C:\QehcAI\Temp\tesseract\tesseract.exe",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)

REQUIRED_LANGS = ("eng", "ara")


def find_tesseract(settings: Settings) -> Path | None:
    configured = (getattr(settings, "tesseract_path", None) or "").strip()
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(_DEFAULT_TESSERACT_CANDIDATES)
    which = shutil.which("tesseract")
    if which:
        candidates.append(which)
    seen: set[str] = set()
    for raw in candidates:
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        path = Path(raw)
        if path.is_file():
            return path
    return None


def _tessdata_dir(exe: Path) -> Path | None:
    sibling = exe.parent / "tessdata"
    if sibling.is_dir():
        return sibling
    env = (os.environ.get("TESSDATA_PREFIX") or "").strip()
    if env:
        path = Path(env)
        if path.is_dir():
            return path
    return None


def list_languages(settings: Settings) -> list[str]:
    exe = find_tesseract(settings)
    if exe is None:
        return []
    env = os.environ.copy()
    tessdata = _tessdata_dir(exe)
    if tessdata is not None:
        # UB Mannheim Windows builds resolve traineddata under TESSDATA_PREFIX itself.
        env["TESSDATA_PREFIX"] = str(tessdata)
    try:
        completed = subprocess.run(
            [str(exe), "--list-langs"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = (completed.stdout or completed.stderr or "").splitlines()
    langs: list[str] = []
    for line in lines:
        token = line.strip().replace("\\", "/")
        if not token or token.lower().startswith("list of"):
            continue
        # Some Windows builds print "tessdata\eng" instead of "eng".
        if "/" in token:
            token = token.rsplit("/", 1)[-1]
        if token.endswith(".traineddata"):
            token = token[: -len(".traineddata")]
        if token and token not in langs:
            langs.append(token)
    # Fallback: inspect tessdata directory when --list-langs is noisy.
    if not any(lang in langs for lang in REQUIRED_LANGS) and exe is not None:
        tessdata = _tessdata_dir(exe)
        if tessdata is not None:
            for path in tessdata.glob("*.traineddata"):
                code = path.stem
                if code and code not in langs:
                    langs.append(code)
    return langs


def ocr_status(settings: Settings) -> dict[str, Any]:
    exe = find_tesseract(settings)
    langs = list_languages(settings) if exe else []
    missing = [lang for lang in REQUIRED_LANGS if lang not in langs]
    ready = exe is not None and not missing
    install_dir = r"C:\QehcAI\Temp\tesseract"
    return {
        "available": ready,
        "engine": "tesseract" if exe else None,
        "path": str(exe) if exe else None,
        "languages": langs,
        "requiredLanguages": list(REQUIRED_LANGS),
        "missingLanguages": missing,
        "installHint": (
            None
            if ready
            else (
                "Install Tesseract OCR with English and Arabic language data. "
                f"Preferred path: {install_dir}\\tesseract.exe. "
                "Download: https://github.com/UB-Mannheim/tesseract/wiki "
                "Silent example: tesseract-ocr-w64-setup-*.exe /VERYSILENT /DIR=\"C:\\QehcAI\\Temp\\tesseract\". "
                "Then set QKA_TESSERACT_PATH."
            )
        ),
    }


def ocr_image_bytes(
    settings: Settings,
    payload: bytes,
    *,
    languages: str | None = None,
    timeout_seconds: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """OCR image bytes. Returns (text, meta). Raises RuntimeError when OCR cannot run."""
    status = ocr_status(settings)
    if not status["available"]:
        raise RuntimeError(status["installHint"] or "Tesseract OCR is not available.")
    exe = Path(status["path"])
    langs = languages or "+".join(lang for lang in REQUIRED_LANGS if lang in status["languages"])
    if not langs:
        langs = "eng"
    timeout = int(timeout_seconds or getattr(settings, "ocr_timeout_seconds", 120) or 120)
    max_pixels = int(getattr(settings, "ocr_max_pixels", 40_000_000) or 40_000_000)
    tmp_root = Path(tempfile.mkdtemp(prefix="qka-ocr-"))
    try:
        image_path = tmp_root / "page.png"
        # Normalize via Pillow so PDF renders / exotic formats become OCR-friendly PNG.
        from PIL import Image
        import io

        with Image.open(io.BytesIO(payload)) as img:
            width, height = img.size
            if width * height > max_pixels:
                raise RuntimeError(f"Image exceeds OCR pixel limit ({width}x{height}).")
            converted = img.convert("RGB")
            converted.save(image_path, format="PNG")
        env = os.environ.copy()
        tessdata = _tessdata_dir(exe)
        if tessdata is not None:
            env["TESSDATA_PREFIX"] = str(tessdata)
        out_base = tmp_root / "out"
        completed = subprocess.run(
            [str(exe), str(image_path), str(out_base), "-l", langs, "--psm", "3"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            cwd=str(exe.parent),
        )
        out_file = Path(str(out_base) + ".txt")
        if completed.returncode != 0 or not out_file.is_file():
            detail = (completed.stderr or completed.stdout or "").strip()[:400]
            raise RuntimeError(detail or "Tesseract OCR failed.")
        text = out_file.read_text(encoding="utf-8", errors="replace")
        return text, {
            "engine": "tesseract",
            "languages": langs,
            "uncertain": True,
            "note": "OCR text may be uncertain for amounts, dates, quantities, and reference numbers.",
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
