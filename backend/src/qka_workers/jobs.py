from __future__ import annotations

from enum import StrEnum


class JobType(StrEnum):
    EXTRACT_EXCEL = "extract.excel"
    EXTRACT_WORD = "extract.word"
    EXTRACT_PDF = "extract.pdf"
    EXTRACT_POWERPOINT = "extract.powerpoint"
    INDEX_CONTENT = "index.content"
    GENERATE_REPORT = "report.generate"
    SYNOLOGY_SYNC = "synology.sync"


REGISTERED_JOBS = tuple(JobType)
