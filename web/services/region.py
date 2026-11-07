"""Content-based master-data region detection (lightweight port of
bunker/android/tools/region.py::master_edition).

The game ships two releases — JP (日服) and EN (全球服) — each with its own
master-data bin. The canonical file names differ (JP=20240405123139.bin.e,
EN=20240404193219.bin.e) but the FILE CONTENT is the authority: a download
manager may rename a second fetch to "…bin (1).e", and a wrong file could be
staged under either name.  This module reads the encrypted bin's TOC and
m_mission rows to answer "jp" or "en", refusing anything that is not master
data at all (returns None).

Detection logic (same as region.master_edition):
  1. Size gate  — refuse unread when outside (1 MB, 64 MB).
  2. Fast EN hash — the known-good EN master has a pinned SHA-256.
  3. AES-CBC decrypt + msgpack TOC parse; require the six anchor tables.
  4. Decode m_mission (LZ4-ext aware) and look for JP_ONLY_MISSIONS
     {(10,198,0),(6,201,0),(29,7,0),(1,101030801,0)} at columns
     MISSION_TYPE=7, MISSION_OPTION=10, MISSION_DETAIL=15.
  5. Any hit → "jp"; a sound master without them → "en".
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from web.services import masterdata_bin

# --- size gates (same thresholds as bunker/android/tools/region.py) ---
MAX_MASTER_SIZE = 64 << 20      # 64 MB — a resource dump is refused unread
MIN_MASTER_SIZE = 1_000_000     # 1 MB  — below this it is not master data

# The known-good EN master's size and SHA-256 (content identity, not name).
_EN_SIZE = 7_801_104
_EN_SHA256 = "60555788242de9a0580b0d86f6d109d67bd9cabd16d7bcda9b9a99745f270778"

# Mission conditions that only the 20240405123139 Japanese master carries
# (scripts/gen_mission_rules.py regional_rules).  The Global master's
# neighbouring counters are 197/199 and 200/202, never 198/201.
JP_ONLY_MISSIONS = frozenset({(10, 198, 0), (6, 201, 0), (29, 7, 0), (1, 101030801, 0)})

# m_mission columns read by position (lunar-scripts-zh/shared/schemas.json).
_MISSION_TYPE = 7
_MISSION_OPTION = 10
_MISSION_DETAIL = 15

# Tables every real master must carry (the six "anchor" tables).
_REQUIRED_TABLES = frozenset({
    "m_costume", "m_weapon", "m_event_quest_chapter",
    "m_shop", "m_mom_banner", "m_mission",
})

# Canonical file names per region — the server's regionMasterData fetches by
# these exact names (the filename invariant).
CANONICAL: dict[str, str] = {
    "jp": "20240405123139.bin.e",
    "en": "20240404193219.bin.e",
}


def canonical_name(region: str) -> str:
    """The canonical bin.e file name for a region key ("jp" / "en")."""
    try:
        return CANONICAL[region]
    except KeyError:
        raise ValueError(f"unknown region {region!r}; expected one of {sorted(CANONICAL)}")


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def master_edition(path: Path | str) -> str | None:
    """Which release's master data a file holds ("en" / "jp"), from its
    CONTENTS; None when it is not master data at all.

    Mirrors bunker/android/tools/region.py::master_edition but reuses
    masterdata_bin's decrypt/parse helpers.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return None
    if not MIN_MASTER_SIZE < size < MAX_MASTER_SIZE:
        return None

    # Fast path: the known-good EN master by size + hash.
    if size == _EN_SIZE and _sha256_of(p) == _EN_SHA256:
        return "en"

    # Full content check: decrypt, parse TOC, decode m_mission.
    try:
        raw = p.read_bytes()
        decrypted = masterdata_bin._decrypt(raw)
        toc, blob = masterdata_bin._parse(decrypted)
        if not isinstance(toc, dict) or not blob:
            return None
        if not _REQUIRED_TABLES.issubset(toc):
            return None
        for offset, length in toc.values():
            if offset < 0 or length <= 0 or offset + length > len(blob):
                return None

        # Decode m_mission rows and scan for JP-only mission signatures.
        import msgpack
        offset, length = toc["m_mission"]
        obj = msgpack.unpackb(bytes(blob[offset:offset + length]),
                              raw=False, strict_map_key=False)
        if isinstance(obj, msgpack.ExtType) and obj.code == 99:
            # LZ4 ext: int32/uint32 big-endian uncompressed size + payload.
            import lz4.block
            tag = obj.data[0]
            if tag == 0xd2:
                ulen = int.from_bytes(obj.data[1:5], "big", signed=True)
                packed = obj.data[5:]
            elif tag == 0xce:
                ulen = int.from_bytes(obj.data[1:5], "big")
                packed = obj.data[5:]
            elif tag <= 0x7f:
                ulen = tag
                packed = obj.data[1:]
            else:
                return None
            rows = msgpack.unpackb(lz4.block.decompress(packed, uncompressed_size=ulen),
                                   raw=False, strict_map_key=False)
        else:
            rows = obj if isinstance(obj, list) else [obj]

        for row in rows:
            if (isinstance(row, list) and len(row) > _MISSION_DETAIL
                    and (row[_MISSION_TYPE], row[_MISSION_OPTION], row[_MISSION_DETAIL])
                    in JP_ONLY_MISSIONS):
                return "jp"
        return "en"
    except Exception:
        return None


def detect_region(path: Path | str) -> str | None:
    """Alias for master_edition — the region of a master-data file, or None."""
    return master_edition(path)
