"""PostgreSQL storage-compatibility restriction (NOT a contract rule; the JSON Schema is unchanged).

PostgreSQL text and jsonb cannot hold U+0000. The JSON Schema allows it inside free strings, so
without this check such an envelope would pass validation and then fail forever at insert time,
making the agent retry a request that can never succeed. Reporting it as a 422 lets the agent drop it.
"""
from __future__ import annotations

from app.errors import ErrorDetail
from app.ingest.schema import format_path


def find_nul_characters(document: object, _path: tuple = ()) -> list[ErrorDetail]:
    out: list[ErrorDetail] = []
    if isinstance(document, str):
        if "\x00" in document:
            out.append(ErrorDetail(format_path(_path), "String contains U+0000, which PostgreSQL cannot store."))
    elif isinstance(document, list):
        for i, item in enumerate(document):
            out.extend(find_nul_characters(item, _path + (i,)))
    elif isinstance(document, dict):
        for key, item in document.items():
            if "\x00" in key:
                out.append(ErrorDetail(format_path(_path), "Object key contains U+0000, which PostgreSQL cannot store."))
            out.extend(find_nul_characters(item, _path + (key,)))
    return out
