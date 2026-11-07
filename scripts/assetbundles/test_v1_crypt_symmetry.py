#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0x31 (octo v1) crypt symmetry regression — upstream recurrence guard.

Asserts that the fixed VERSION1 branch of encrypt_assetbundle.py is strictly
symmetric with the read side (extract_names.decrypt_text_bundle / the game's
octo v1 reader: restore file[0..255] = 255 content bytes):

  * 0x31 原版包 decrypt -> encrypt -> decrypt 回环逐字节一致,
    and the re-encrypted bytes reproduce the original on-disk file exactly
    (the fix_help_repack.encrypt_fixed assertion);
  * the mask span is exactly file[1..255] — file[256] stays in plain text;
  * decrypt_assetbundle.decrypt_bundle (restore side) agrees with
    decrypt_text_bundle on original and re-encrypted bundles;
  * the 0x32 (v1full) path is byte-identical to the pre-fix implementation.

Run:  python test_v1_crypt_symmetry.py
Exit: 0 = all assertions passed, 1 = failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LUNAR_BASE = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(LUNAR_BASE / "tools"))

from assetbundle_crypt import (  # noqa: E402
    DEFAULT_HEADER_LENGTH,
    VERSION1,
    VERSION1_FULL,
    string_to_mask_bytes,
    xor_unmask,
)
import decrypt_assetbundle  # noqa: E402
import encrypt_assetbundle  # noqa: E402
import extract_names as reader  # noqa: E402

REPO = LUNAR_BASE.parent
# 0x31 originals (deploy backup; read-only)
HELP_ROOT = REPO / "zh-l10n" / "work" / "device" / "deploy-20261025-210555" / "backup" / "assetbundle-ui-help" / "ja"
HELP_MASK_PREFIX = "ui)help)ja"
# 0x32 text bundles (server tree; read-only)
TEXT_ROOT = REPO / "lunar-tear" / "server" / "assets" / "revisions" / "0" / "assetbundle" / "text"

FAILURES: list[str] = []
CHECKS = 0


def check(ok: bool, label: str) -> bool:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        FAILURES.append(label)
    return ok


def encrypt_bundle_pre_fix(raw: bytes, mask_name: str, version: int = VERSION1_FULL,
                           header_length: int = DEFAULT_HEADER_LENGTH) -> bytes:
    """Pre-fix reference (masks 256 content bytes in the VERSION1 branch)."""
    mask = string_to_mask_bytes(mask_name)
    data = bytearray(raw[1:])
    if version == VERSION1_FULL:
        xor_unmask(data, mask, len(data), mask_offset=1)
    elif version == VERSION1:
        xor_unmask(data, mask, header_length, mask_offset=1)
    return bytes([version]) + bytes(data)


def help_mask_name(rel: Path) -> str:
    return HELP_MASK_PREFIX + ")" + rel.with_suffix("").as_posix().replace("/", ")")


# ---------------------------------------------------------------------------
# 1) mask-span unit check on a synthetic UnityFS-shaped stream
# ---------------------------------------------------------------------------
def test_mask_span() -> None:
    print("[1] 0x31 mask span (synthetic stream)")
    raw = b"UnityFS" + bytes(range(256)) * 4  # >= 800 B
    mask_name = "synthetic)bundle)name"
    mask = string_to_mask_bytes(mask_name)

    enc = encrypt_assetbundle.encrypt_bundle(raw, mask_name, VERSION1)
    check(enc[0] == 0x31, "version tag 0x31 written at file[0]")

    span_ok = all(enc[j] == raw[j] ^ mask[j % len(mask)] for j in range(1, DEFAULT_HEADER_LENGTH))
    check(span_ok, f"file[1..{DEFAULT_HEADER_LENGTH - 1}] masked with mask[1..{DEFAULT_HEADER_LENGTH - 1}] (255 content bytes)")
    check(enc[DEFAULT_HEADER_LENGTH] == raw[DEFAULT_HEADER_LENGTH],
          f"file[{DEFAULT_HEADER_LENGTH}] stays in plain text (the pre-fix crash byte)")
    check(enc[DEFAULT_HEADER_LENGTH + 1:] == raw[DEFAULT_HEADER_LENGTH + 1:], "tail untouched")

    pre = encrypt_bundle_pre_fix(raw, mask_name, VERSION1)
    diff = [i for i in range(min(len(pre), len(enc))) if pre[i] != enc[i]]
    check(diff == [DEFAULT_HEADER_LENGTH],
          f"vs pre-fix encrypt: single delta at file[{DEFAULT_HEADER_LENGTH}] ({diff})")

    dec = reader.decrypt_text_bundle(enc, mask_name)
    check(dec == raw, "decrypt_text_bundle(encrypt(raw)) == raw (strict symmetry)")
    check(decrypt_assetbundle.decrypt_bundle(enc, mask_name) == raw,
          "decrypt_assetbundle.decrypt_bundle(encrypt(raw)) == raw")


# ---------------------------------------------------------------------------
# 2) real 0x31 originals: decrypt -> encrypt -> decrypt byte-identical
# ---------------------------------------------------------------------------
def test_v1_originals() -> None:
    print("[2] 0x31 原版包 decrypt→encrypt→decrypt 回环")
    files = sorted(HELP_ROOT.rglob("*.assetbundle")) if HELP_ROOT.is_dir() else []
    if not files:
        check(False, f"0x31 originals present under {HELP_ROOT}")
        return
    print(f"      originals: {len(files)} bundles under {HELP_ROOT}")

    rt_bad: list[str] = []
    replay_bad: list[str] = []
    restore_bad: list[str] = []
    for f in files:
        rel = f.relative_to(HELP_ROOT)
        mask = help_mask_name(rel)
        disk = f.read_bytes()
        if disk[0] != 0x31:
            rt_bad.append(f"{rel}: first byte 0x{disk[0]:02x} != 0x31")
            continue

        dec1 = reader.decrypt_text_bundle(disk, mask)
        if dec1[:7] != b"UnityFS":
            rt_bad.append(f"{rel}: decrypt_text_bundle header {dec1[:7]!r}")
            continue

        enc = encrypt_assetbundle.encrypt_bundle(dec1, mask, VERSION1)
        if enc != disk:
            replay_bad.append(f"{rel}: encrypt(decrypt(orig)) != orig")
        dec2 = reader.decrypt_text_bundle(enc, mask)
        if dec2 != dec1:
            rt_bad.append(f"{rel}: decrypt→encrypt→decrypt differs from decrypt")
        if decrypt_assetbundle.decrypt_bundle(disk, mask) != dec1:
            restore_bad.append(f"{rel}: decrypt_assetbundle != decrypt_text_bundle on original")

    check(not rt_bad, f"回环逐字节一致 for {len(files)} 0x31 originals" + (f" ({rt_bad[:3]})" if rt_bad else ""))
    check(not replay_bad, f"encrypt(decrypt(orig)) == orig for {len(files)} 0x31 originals" + (f" ({replay_bad[:3]})" if replay_bad else ""))
    check(not restore_bad, f"restore side == read side on {len(files)} originals" + (f" ({restore_bad[:3]})" if restore_bad else ""))

    # headline single-package check (fix_help_repack reference package)
    rel = Path("category_01/contents.assetbundle")
    f = HELP_ROOT / rel
    if f.is_file():
        disk = f.read_bytes()
        mask = help_mask_name(rel)
        dec1 = reader.decrypt_text_bundle(disk, mask)
        enc = encrypt_assetbundle.encrypt_bundle(dec1, mask, VERSION1)
        dec2 = reader.decrypt_text_bundle(enc, mask)
        check(enc == disk, f"reference package {rel}: re-encrypt == original bytes")
        check(dec2 == dec1, f"reference package {rel}: decrypt→encrypt→decrypt 逐字节一致")


# ---------------------------------------------------------------------------
# 3) 0x32 (v1full) regression: path unchanged
# ---------------------------------------------------------------------------
def test_v1full_regression() -> None:
    print("[3] 0x32 v1full 路径回归不变")
    raw = b"UnityFS" + bytes(range(256)) * 4
    mask_name = "synthetic)bundle)name"
    mask = string_to_mask_bytes(mask_name)

    enc = encrypt_assetbundle.encrypt_bundle(raw, mask_name, VERSION1_FULL)
    pre = encrypt_bundle_pre_fix(raw, mask_name, VERSION1_FULL)
    check(enc == pre, "0x32 encrypt output bit-identical to pre-fix implementation")
    check(enc[0] == 0x32, "version tag 0x32 at file[0]")
    check(all(enc[j] == raw[j] ^ mask[j % len(mask)] for j in range(1, len(raw))), "0x32 masks every content byte")
    check(reader.decrypt_text_bundle(enc, mask_name) == raw, "0x32 decrypt_text_bundle roundtrip == raw")
    check(decrypt_assetbundle.decrypt_bundle(enc, mask_name) == raw, "0x32 decrypt_assetbundle roundtrip == raw")

    samples = sorted(TEXT_ROOT.rglob("*.assetbundle"))[:5] if TEXT_ROOT.is_dir() else []
    ok = True
    from assetbundle_crypt import derive_mask_name
    for f in samples:
        disk = f.read_bytes()
        if disk[0] != 0x32:
            continue
        mask_n = derive_mask_name(str(f))
        if not mask_n:
            ok = False
            continue
        dec = reader.decrypt_text_bundle(disk, mask_n)
        enc = encrypt_assetbundle.encrypt_bundle(dec, mask_n, VERSION1_FULL)
        if enc != disk or reader.decrypt_text_bundle(enc, mask_n) != dec:
            ok = False
            break
    check(ok, f"0x32 real bundles ({len(samples)} sampled from text tree): re-encrypt == original, roundtrip stable")


def main() -> int:
    print(f"python {sys.version.split()[0]}  encrypt_assetbundle={encrypt_assetbundle.__file__}")
    test_mask_span()
    test_v1_originals()
    test_v1full_regression()
    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
