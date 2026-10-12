"""Decode subprocess / log bytes that may be UTF-8 or the local ANSI codepage.

Windows console tools (pip, go, netstat, taskkill, ...) write GBK/CP936 on a
Chinese Windows, while Linux tools write UTF-8. Decoding everything as UTF-8
turns the Chinese output into U+FFFD mojibake in the panel UI, so try UTF-8
first and fall back to the locale's preferred encoding.
"""

from __future__ import annotations

import locale
import sys


def preferred_encoding() -> str:
    """Encoding the local console/tools use when they are not UTF-8."""
    if sys.platform == "win32":
        try:
            return locale.getpreferredencoding(False) or "gbk"
        except Exception:
            return "gbk"
    return "utf-8"


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8", preferred_encoding()):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def decode_lines(data: bytes) -> list[str]:
    """Split on LF and decode each line separately, so a log that mixes
    UTF-8 and CP936 lines still renders correctly."""
    parts = data.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()  # same as splitlines(): no trailing empty entry
    return [decode_bytes(raw).rstrip("\r") for raw in parts]
