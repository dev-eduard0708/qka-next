"""Detect locally installed Microsoft 365 / Office desktop apps.

Read-only detection only. Never launches Office, never uses COM automation,
and never starts Excel/Word/PowerPoint from API or worker processes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_OFFICE16_CANDIDATES = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft Office" / "root" / "Office16",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Office" / "root" / "Office16",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft Office" / "Office16",
)

_APP_FILES = {
    "excel": ("EXCEL.EXE", "Microsoft Excel"),
    "word": ("WINWORD.EXE", "Microsoft Word"),
    "powerpoint": ("POWERPNT.EXE", "Microsoft PowerPoint"),
}


def _file_version(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(buf, r"\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        info = ctypes.cast(pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        major = info.dwFileVersionMS >> 16
        minor = info.dwFileVersionMS & 0xFFFF
        build = info.dwFileVersionLS >> 16
        revision = info.dwFileVersionLS & 0xFFFF
        return f"{major}.{minor}.{build}.{revision}"
    except Exception:
        return None


def _click_to_run_version() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "VersionToReport")
            return str(value) if value else None
    except OSError:
        return None


def _product_release_ids() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "ProductReleaseIds")
            return str(value) if value else None
    except OSError:
        return None


def detect_microsoft_office() -> dict[str, Any]:
    """Return detected desktop Office apps. Does not launch or modify Office."""
    apps: dict[str, Any] = {}
    install_root: str | None = None
    for folder in _OFFICE16_CANDIDATES:
        if not folder.is_dir():
            continue
        install_root = str(folder)
        for key, (exe_name, label) in _APP_FILES.items():
            path = folder / exe_name
            if path.is_file():
                apps[key] = {
                    "available": True,
                    "label": label,
                    "path": str(path),
                    "version": _file_version(path),
                }
            else:
                apps.setdefault(
                    key,
                    {"available": False, "label": label, "path": None, "version": None},
                )
        break
    for key, (_exe_name, label) in _APP_FILES.items():
        apps.setdefault(key, {"available": False, "label": label, "path": None, "version": None})

    version = _click_to_run_version() or next(
        (apps[k]["version"] for k in ("excel", "word", "powerpoint") if apps[k].get("version")),
        None,
    )
    product_ids = _product_release_ids()
    available = any(apps[k]["available"] for k in apps)
    return {
        "available": available,
        "edition": "Microsoft 365" if product_ids and "O365" in product_ids else ("Microsoft Office" if available else None),
        "productReleaseIds": product_ids,
        "version": version,
        "installRoot": install_root,
        "apps": apps,
        "launchPolicy": (
            "Desktop Office is opened only via authorized browser download of original bytes "
            "and the user's registered Windows file associations. "
            "QKA never uses Excel/Word/PowerPoint COM automation from API or workers."
        ),
    }


def microsoft_graph_preview_feasibility() -> dict[str, Any]:
    """Graph web preview is only applicable to files already in approved SharePoint/OneDrive."""
    return {
        "status": "NOT_AVAILABLE",
        "reason": (
            "QKA Synology originals are not stored in SharePoint or OneDrive for Business. "
            "Microsoft Graph preview APIs apply only to Graph-backed drive items. "
            "QKA will not upload or copy Synology documents to Microsoft cloud without "
            "explicit administrator approval."
        ),
        "localOfficeBrowserApi": False,
        "note": (
            "Locally installed Microsoft 365 does not provide a browser rendering API "
            "for arbitrary Synology files. Keep LibreOffice/PDF page preview for in-browser "
            "original-format viewing, and Open With for desktop Office."
        ),
    }


def open_with_label(extension: str) -> str:
    ext = (extension or "").lower().lstrip(".")
    if ext in {"xlsx", "xls", "xlsm", "xlsb", "csv"}:
        return "Open with Microsoft Excel"
    if ext in {"docx", "doc"}:
        return "Open with Microsoft Word"
    if ext in {"pptx", "ppt"}:
        return "Open with Microsoft PowerPoint"
    if ext == "pdf":
        return "Open with default PDF application"
    return "Open with default application"


def recommended_app(extension: str) -> str:
    ext = (extension or "").lower().lstrip(".")
    if ext in {"xlsx", "xls", "xlsm", "xlsb", "csv"}:
        return "Microsoft Excel"
    if ext in {"docx", "doc"}:
        return "Microsoft Word"
    if ext in {"pptx", "ppt"}:
        return "Microsoft PowerPoint"
    if ext == "pdf":
        return "default PDF application"
    if ext in {"txt", "md", "log"}:
        return "default text editor"
    if ext in {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff"}:
        return "default image viewer"
    return "default application"
