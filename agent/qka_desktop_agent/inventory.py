"""Lightweight Windows inventory for QKA Desktop Agent (no extra packages).

Uses stdlib + optional pywin32 WMI. Never modifies NTFS ACLs. Never reads file
contents. Volume discovery lists drive roots only — no recursive enumeration.
"""

from __future__ import annotations

import logging
import os
import platform
import socket
from pathlib import Path
from typing import Any

log = logging.getLogger("qka.agent.inventory")


def collect_inventory(
    *,
    include_hardware: bool = True,
    include_volumes: bool = True,
    employee_sid: str = "",
    agent_version: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agentVersion": agent_version,
        "includeHardware": include_hardware,
        "includeVolumes": include_volumes,
    }
    if include_hardware:
        payload["hardware"] = _collect_hardware()
        payload["disks"] = _collect_disks()
    if include_volumes:
        payload["volumes"] = _collect_volumes()
        payload["knownFolders"] = _collect_known_folders(employee_sid)
    return payload


def _collect_hardware() -> dict[str, Any]:
    hw = {
        "hostname": os.environ.get("COMPUTERNAME") or socket.gethostname(),
        "domainName": os.environ.get("USERDOMAIN") or "",
        "osEdition": "",
        "osVersion": platform.version(),
        "osBuild": "",
        "manufacturer": "",
        "model": "",
        "serialNumber": "",
        "cpuModel": platform.processor() or "",
        "cpuPhysicalCores": os.cpu_count() or 0,
        "cpuLogicalProcessors": os.cpu_count() or 0,
        "ramBytes": 0,
    }
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        ) as key:
            hw["osEdition"] = str(winreg.QueryValueEx(key, "ProductName")[0])
            hw["osBuild"] = str(winreg.QueryValueEx(key, "CurrentBuild")[0])
            try:
                hw["osVersion"] = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
            except OSError:
                hw["osVersion"] = str(winreg.QueryValueEx(key, "ReleaseId")[0])
    except Exception as exc:  # noqa: BLE001
        log.info("os_registry_unavailable err=%s", exc)

    wmi = _wmi()
    if wmi is None:
        hw["ramBytes"] = _ram_bytes_ctypes()
        return hw
    try:
        for cs in wmi.ExecQuery("SELECT Manufacturer, Model, TotalPhysicalMemory, NumberOfProcessors FROM Win32_ComputerSystem"):
            hw["manufacturer"] = str(getattr(cs, "Manufacturer", "") or "")
            hw["model"] = str(getattr(cs, "Model", "") or "")
            try:
                hw["ramBytes"] = int(getattr(cs, "TotalPhysicalMemory", 0) or 0)
            except (TypeError, ValueError):
                pass
            break
    except Exception as exc:  # noqa: BLE001
        log.info("wmi_computersystem_failed err=%s", exc)
        hw["ramBytes"] = _ram_bytes_ctypes()
    try:
        for bios in wmi.ExecQuery("SELECT SerialNumber FROM Win32_BIOS"):
            hw["serialNumber"] = str(getattr(bios, "SerialNumber", "") or "")
            break
    except Exception as exc:  # noqa: BLE001
        log.info("wmi_bios_failed err=%s", exc)
    try:
        for cpu in wmi.ExecQuery(
            "SELECT Name, NumberOfCores, NumberOfLogicalProcessors FROM Win32_Processor"
        ):
            hw["cpuModel"] = str(getattr(cpu, "Name", "") or hw["cpuModel"])
            hw["cpuPhysicalCores"] = int(getattr(cpu, "NumberOfCores", 0) or hw["cpuPhysicalCores"] or 0)
            hw["cpuLogicalProcessors"] = int(
                getattr(cpu, "NumberOfLogicalProcessors", 0) or hw["cpuLogicalProcessors"] or 0
            )
            break
    except Exception as exc:  # noqa: BLE001
        log.info("wmi_processor_failed err=%s", exc)
    if not hw["ramBytes"]:
        hw["ramBytes"] = _ram_bytes_ctypes()
    return hw


def _collect_disks() -> list[dict[str, Any]]:
    disks: list[dict[str, Any]] = []
    wmi = _wmi()
    if wmi is None:
        return disks
    try:
        for d in wmi.ExecQuery(
            "SELECT Model, Size, InterfaceType, SerialNumber, MediaType, Index FROM Win32_DiskDrive"
        ):
            try:
                size = int(getattr(d, "Size", 0) or 0)
            except (TypeError, ValueError):
                size = 0
            disks.append(
                {
                    "index": int(getattr(d, "Index", 0) or 0),
                    "model": str(getattr(d, "Model", "") or ""),
                    "serialNumber": str(getattr(d, "SerialNumber", "") or "").strip(),
                    "interfaceType": str(getattr(d, "InterfaceType", "") or ""),
                    "mediaType": str(getattr(d, "MediaType", "") or ""),
                    "sizeBytes": size,
                }
            )
    except Exception as exc:  # noqa: BLE001
        log.info("wmi_diskdrive_failed err=%s", exc)
    return disks


def _collect_volumes() -> list[dict[str, Any]]:
    volumes: list[dict[str, Any]] = []
    wmi = _wmi()
    if wmi is not None:
        try:
            for disk in wmi.ExecQuery(
                "SELECT DeviceID, VolumeName, FileSystem, Size, FreeSpace, DriveType FROM Win32_LogicalDisk WHERE DriveType=3"
            ):
                letter = str(getattr(disk, "DeviceID", "") or "").upper()
                if not letter:
                    continue
                try:
                    capacity = int(getattr(disk, "Size", 0) or 0)
                    free = int(getattr(disk, "FreeSpace", 0) or 0)
                except (TypeError, ValueError):
                    capacity, free = 0, 0
                root = letter if letter.endswith("\\") else f"{letter}\\"
                accessible = False
                try:
                    accessible = Path(root).exists()
                    if accessible:
                        next(Path(root).iterdir(), None)
                        accessible = True
                except PermissionError:
                    accessible = False
                except OSError:
                    accessible = Path(root).exists()
                volumes.append(
                    {
                        "letter": letter.rstrip("\\"),
                        "rootPath": root,
                        "label": str(getattr(disk, "VolumeName", "") or ""),
                        "fileSystem": str(getattr(disk, "FileSystem", "") or ""),
                        "capacityBytes": capacity,
                        "freeBytes": free,
                        "usedBytes": max(0, capacity - free),
                        "driveType": int(getattr(disk, "DriveType", 3) or 3),
                        "accessible": accessible,
                    }
                )
            if volumes:
                return volumes
        except Exception as exc:  # noqa: BLE001
            log.info("wmi_logicaldisk_failed err=%s", exc)

    # Fallback: enumerate drive letters via GetLogicalDrives bitmask.
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if not (mask & (1 << i)):
                continue
            letter = f"{chr(ord('A') + i)}:"
            root = f"{letter}\\"
            try:
                dtype = ctypes.windll.kernel32.GetDriveTypeW(root)
            except Exception:
                dtype = 0
            if dtype != 3:  # DRIVE_FIXED
                continue
            total, free = _disk_space(root)
            accessible = False
            try:
                accessible = Path(root).exists()
                next(Path(root).iterdir(), None)
                accessible = True
            except OSError:
                accessible = Path(root).exists()
            volumes.append(
                {
                    "letter": letter,
                    "rootPath": root,
                    "label": "",
                    "fileSystem": "",
                    "capacityBytes": total,
                    "freeBytes": free,
                    "usedBytes": max(0, total - free),
                    "driveType": 3,
                    "accessible": accessible,
                }
            )
    except Exception as exc:  # noqa: BLE001
        log.info("drive_bitmask_failed err=%s", exc)
    return volumes


def _collect_known_folders(employee_sid: str) -> list[dict[str, Any]]:
    if not (employee_sid or "").strip():
        return []
    try:
        from qka_desktop_agent.folder_browser import nodes_to_json, resolve_known_folders

        nodes, _err = resolve_known_folders(employee_sid.strip())
        return nodes_to_json(nodes)
    except Exception as exc:  # noqa: BLE001
        log.info("known_folders_failed err=%s", exc)
        return []


def _wmi():
    if os.name != "nt":
        return None
    try:
        import win32com.client  # type: ignore

        return win32com.client.GetObject("winmgmts:")
    except Exception:
        return None


def _ram_bytes_ctypes() -> int:
    if os.name != "nt":
        return 0
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return int(stat.ullTotalPhys)
    except Exception:
        return 0
    return 0


def _disk_space(root: str) -> tuple[int, int]:
    try:
        import ctypes

        free = ctypes.c_ulonglong(0)
        total = ctypes.c_ulonglong(0)
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(root),
            None,
            ctypes.byref(total),
            ctypes.byref(free),
        )
        if ok:
            return int(total.value), int(free.value)
    except Exception:
        pass
    return 0, 0
