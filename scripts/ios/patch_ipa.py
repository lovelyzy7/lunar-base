#!/usr/bin/env python3
"""
patch_ipa.py — Static patcher for NieR Re[in]carnation IPA.

Patches an unpacked Payload/NieR.app so the game connects to a private
server without any runtime (Frida) hooks.

Patches applied:
  1. global-metadata.dat                — rewrite IL2CPP string literals (URLs + hostname)
  2. UnityFramework (Mach-O ARM64)      — SSL bypass, encryption passthrough,
                                          Octo plain list, IAP bypass, EOS bypass
  3. Info.plist                         — drop UISupportedDevices allowlist
  4. Frameworks/FBSDK*.framework/*      — redirect Facebook SDK OAuth to --auth-host:
                                            * base domain rewrite (with merge into
                                              .facebook.com slot for >12-char hosts)
                                            * _facebookURLWithHostPrefix nil-out
                                              (drops m./graph./graph-video. prefix)
                                            * https->http + native-SSO scheme neuter
                                            * useSafariViewControllerForDialogName:
                                              force-NO on both FBSDKServerConfiguration
                                              and FBSDKServerConfigurationProvider
                                              (forces plain Safari via
                                              UIApplication.openURL for OAuth so the
                                              fb<appid>:// redirect bounces back via
                                              application:openURL:options:; sidesteps
                                              the iOS 18+ ASWebAuthenticationSession /
                                              SFSafariViewController callback regression
                                              against private LAN hosts)
                                            * LoginManager.completeAuthentication
                                              validateReauthentication bypass
                                            * LoginManager.applicationDidBecomeActive
                                              neuter (kills the implicit-cancel race
                                              that breaks the first login attempt)
                                            * FBAEMKit graph URL byte-replace
  5. Repack patched tree back into .ipa
"""

import argparse
import os
import plistlib
import shutil
import struct
import sys
import tempfile
import zipfile

# ---------------------------------------------------------------------------
# global-metadata.dat string literal patching
# ---------------------------------------------------------------------------

METADATA_MAGIC = 0xFAB11BAF

# Header offsets (v24): each section is a (uint32 offset, uint32 size) pair
HDR_STRING_LITERAL_OFF = 8  # stringLiteral table
HDR_STRING_LITERAL_DATA_OFF = 16  # stringLiteralData blob


def patch_metadata_strings(meta_path: str, replacements: list[tuple[str, str]]) -> int:
    with open(meta_path, "rb") as f:
        data = bytearray(f.read())

    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != METADATA_MAGIC:
        print(f"  [!] Bad magic 0x{magic:08X}, expected 0x{METADATA_MAGIC:08X}")
        return 0

    version = struct.unpack_from("<i", data, 4)[0]
    print(f"  metadata v{version}, {len(data)} bytes")

    sl_off, sl_size = struct.unpack_from("<II", data, HDR_STRING_LITERAL_OFF)
    sld_off, sld_size = struct.unpack_from("<II", data, HDR_STRING_LITERAL_DATA_OFF)
    n_entries = sl_size // 8
    print(f"  stringLiteral: {n_entries} entries @ 0x{sl_off:X}")
    print(f"  stringLiteralData: {sld_size} bytes @ 0x{sld_off:X}")

    patched = 0
    for old_str, new_str in replacements:
        old_bytes = old_str.encode("utf-8")
        new_bytes = new_str.encode("utf-8")

        if len(new_bytes) > len(old_bytes):
            print(
                f"  [!] SKIP: replacement longer than original "
                f"({len(new_bytes)} > {len(old_bytes)}): {old_str!r}"
            )
            continue

        # Find the string in the data blob
        blob_pos = data.find(old_bytes, sld_off, sld_off + sld_size)
        if blob_pos < 0:
            print(f"  [!] NOT FOUND in blob: {old_str!r}")
            continue

        data_index = blob_pos - sld_off

        # Find the StringLiteral table entry that references this exact string
        entry_found = False
        for i in range(n_entries):
            e_off = sl_off + i * 8
            e_len, e_idx = struct.unpack_from("<II", data, e_off)
            if e_idx == data_index and e_len == len(old_bytes):
                # Update length
                struct.pack_into("<I", data, e_off, len(new_bytes))
                entry_found = True
                print(f"  entry #{i}: length {e_len} -> {len(new_bytes)}")
                break

        if not entry_found:
            print(
                f"  [!] No table entry found for {old_str!r} (dataIndex=0x{data_index:X})"
            )
            continue

        # Overwrite string data (pad remainder with null bytes)
        data[blob_pos : blob_pos + len(old_bytes)] = new_bytes + b"\x00" * (
            len(old_bytes) - len(new_bytes)
        )

        print(f"  PATCHED: {old_str!r} -> {new_str!r}")
        patched += 1

    with open(meta_path, "wb") as f:
        f.write(data)

    return patched


# ---------------------------------------------------------------------------
# Info.plist — remove UISupportedDevices allowlist
# ---------------------------------------------------------------------------

# The original IPA's UISupportedDevices list omits iPhone 16/17 and newer
# models, blocking installation. Removing it lets the IPA install on any
# iPhone.


def patch_info_plist(plist_path: str) -> None:
    with open(plist_path, "rb") as f:
        pl = plistlib.load(f)

    if "UISupportedDevices" in pl:
        del pl["UISupportedDevices"]
        with open(plist_path, "wb") as f:
            plistlib.dump(pl, f)
        print("  removed UISupportedDevices allowlist")
    else:
        print("  UISupportedDevices not present, nothing to do")


# ---------------------------------------------------------------------------
# Frameworks/FBSDK*.framework/* — Facebook SDK binary patching
# ---------------------------------------------------------------------------

# The iOS Facebook SDK is bundled as native frameworks (no smali). To redirect
# OAuth at our local auth-server we apply six patch families to FBSDKCoreKit /
# FBSDKLoginKit, plus a single byte-replace in FBAEMKit. All patches are
# source-confirmed against ~/Downloads/facebook-ios-sdk-14.1.0.
#
# FBSDKCoreKit:
#
#   1. Base domain rewrite. The CFString constant "facebook.com" (12 bytes)
#      is overwritten with auth_host. When auth_host > 12 chars, the helper
#      reuses the adjacent ".facebook.com" cstring slot (combined 27 bytes,
#      26 usable + NUL) and zeroes the merge target's CFString length so
#      callers see an empty string. ".facebook.com" is only used by
#      FBSDKAuthenticationTokenClaims's JWT issuer-suffix validation, which
#      our login flow never exercises.
#
#   2. _facebookURLWithHostPrefix: hostPrefix nil-out. The Swift literals
#      "m." / "graph." / "graph-video." passed as hostPrefix at the call
#      site are inlined immediates and therefore can't be reached via
#      CFString tweaks. We instead patch one ARM64 instruction in the
#      Obj-C method's prologue so the hostPrefix argument is forced to nil
#      before the URL is built. Result: dialog URL is just
#      "http://<auth_host>/dialog/oauth" with no "m." subdomain.
#
#   3. CFString length neutering: "https" length 5 -> 4 (NSString reads
#      "http" instead) so all SDK-built URLs use plain HTTP — necessary
#      because our auth-server runs on 0.0.0.0:3000 plain HTTP. Also
#      "fbapi" len -> 0 and "fb-messenger-share-api" len -> 0 so
#      canOpenURL: for native FB / Messenger SSO always fails and the SDK
#      falls back to the in-app browser path we control.
#
#   4. force plain Safari for OAuth. Both
#      -[FBSDKServerConfiguration useSafariViewControllerForDialogName:]
#      and -[FBSDKServerConfigurationProvider useSafariViewControllerFor-
#      DialogName:] are overwritten with `mov w0, #0; ret`.
#      LoginManager.performBrowserLogIn (LoginManager.swift L629–680)
#      gates on this: when YES it goes through FBSDKBridgeAPI which on
#      iOS 18+ uses ASWebAuthenticationSession (mandatory privacy alert;
#      isolated cookie store) or SFSafariViewController, neither of which
#      reliably delivers the redirect callback for our private LAN host.
#      When NO, the SDK calls UIApplication.open(url, options: @{},
#      completionHandler:) — i.e. plain Safari — and the 302 to
#      fb<appid>://authorize/?... bounces back through iOS's standard
#      application:openURL:options: -> FBSDKApplicationDelegate ->
#      _LoginURLCompleter -> Unity OnLoginComplete -> game gRPC.
#      Trade-off: two app switches instead of an in-place sheet, but no
#      privacy alert and a callback path that actually fires.
#
# FBSDKLoginKit:
#
#   5. validateReauthentication bypass. LoginManager.completeAuthentication
#      checks `if result?.token != nil, let accessToken = self.accessToken-
#      Wallet?.current { return validateReauthentication(...) }` — a stale
#      AccessToken.current on iOS makes new-user logins fail with a
#      userMismatch error. We rewrite the gate `cbz x8, +0x80` to an
#      unconditional `b +0x80` so the SDK always falls through to
#      setGlobalProperties + invokeHandler.
#
#   6. applicationDidBecomeActive neuter. This is the actual root cause of
#      "first attempt fails, second works". The SDK installs both an
#      application(_:open:...) URL handler (async; transitions state to
#      .idle on URL arrival) AND an applicationDidBecomeActive observer
#      that synchronously calls handleImplicitCancelOfLogIn() if state ==
#      .performingLogin. With plain-Safari OAuth (#4) the Safari -> NieR
#      app-switch fires applicationDidBecomeActive *before* the URL is
#      delivered, so this race is now guaranteed to hit on every login.
#      We RET-patch BOTH the @objc thunk (what UIApplication notifications
#      ultimately call through the ObjC selector table) AND the Swift
#      impl so neither path can ever invoke handleImplicitCancelOfLogIn.
#      Legitimate cancel paths (non-FB URL, browser-launch error,
#      double login) are unaffected.
#
# FBAEMKit:
#
#   7. "https://graph.facebook.com/v14.0/" -> "http://<auth_host>/v14.0/"
#      (analytics endpoint; failures are non-critical). Plain byte-replace,
#      null-padded.

# ----- Mach-O / CFString helpers -----

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
MH_MAGIC_64 = 0xFEEDFACF


def _parse_macho_sections(data: bytes) -> dict[str, tuple[int, int, int]]:
    """Walk LC_SEGMENT_64 commands and return a map of "segname,sectname"
    -> (file_off, vmaddr, size) for every section. Used to translate
    vmaddrs in __cfstring data pointers into file offsets."""
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != MH_MAGIC_64:
        raise ValueError(f"not a 64-bit Mach-O (magic 0x{magic:08x})")
    ncmds = struct.unpack_from("<I", data, 16)[0]
    off = 32
    sections: dict[str, tuple[int, int, int]] = {}
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmd == LC_SEGMENT_64:
            nsects = struct.unpack_from("<I", data, off + 64)[0]
            soff = off + 72
            for _ in range(nsects):
                sectname = data[soff : soff + 16].split(b"\x00", 1)[0].decode()
                segname = data[soff + 16 : soff + 32].split(b"\x00", 1)[0].decode()
                vmaddr = struct.unpack_from("<Q", data, soff + 32)[0]
                size = struct.unpack_from("<Q", data, soff + 40)[0]
                file_off = struct.unpack_from("<I", data, soff + 48)[0]
                sections[f"{segname},{sectname}"] = (file_off, vmaddr, size)
                soff += 80
        off += cmdsize
    return sections


def _parse_macho_symbols(data: bytes) -> dict[str, int]:
    """Walk LC_SYMTAB and return a map of symbol name -> n_value (vmaddr)
    for every defined symbol with a non-empty name. Used to locate Obj-C
    method IMPs (`-[Class selector:]`) without relying on byte patterns
    over the prologue."""
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != MH_MAGIC_64:
        raise ValueError(f"not a 64-bit Mach-O (magic 0x{magic:08x})")
    ncmds = struct.unpack_from("<I", data, 16)[0]
    off = 32
    symoff = nsyms = stroff = strsize = 0
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmd == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from(
                "<IIII", data, off + 8
            )
            break
        off += cmdsize
    syms: dict[str, int] = {}
    if not symoff or not nsyms:
        return syms
    for i in range(nsyms):
        e = symoff + i * 16
        n_strx, _n_type, _n_sect, _n_desc, n_value = struct.unpack_from(
            "<IBBHQ", data, e
        )
        if n_strx == 0 or n_value == 0:
            continue
        name_start = stroff + n_strx
        name_end = data.find(b"\x00", name_start, stroff + strsize)
        if name_end < 0:
            continue
        name = data[name_start:name_end].decode("utf-8", errors="replace")
        if name:
            syms[name] = n_value
    return syms


def _vmaddr_to_file_off(
    sections: dict[str, tuple[int, int, int]], vmaddr: int
) -> int:
    """Find the section containing vmaddr and return the corresponding
    file offset. Returns -1 if no section covers it."""
    for file_off, sec_vmaddr, size in sections.values():
        if sec_vmaddr <= vmaddr < sec_vmaddr + size:
            return file_off + (vmaddr - sec_vmaddr)
    return -1


def _find_cstring_offset(
    data: bytes, sections: dict[str, tuple[int, int, int]], text: bytes
) -> int:
    """Return the file offset of `text\\0` inside __TEXT,__cstring (where
    `text` starts at a cstring boundary, i.e. is preceded by NUL or by
    the section start). -1 if not found."""
    cstr = sections.get("__TEXT,__cstring")
    if not cstr:
        return -1
    file_off, _vm, size = cstr
    needle = text + b"\x00"
    pos = file_off
    end = file_off + size
    while pos < end:
        i = data.find(needle, pos, end)
        if i < 0:
            return -1
        if i == file_off or data[i - 1] == 0:
            return i
        pos = i + 1
    return -1


def _set_cfstring_length(
    data: bytearray,
    sections: dict[str, tuple[int, int, int]],
    target_vmaddr: int,
    new_len: int,
) -> bool:
    """Find the __cfstring 32-byte struct whose data_ptr field equals
    target_vmaddr and overwrite its length field (offset +24) with new_len.
    Returns True on success."""
    cfstr = sections.get("__DATA,__cfstring") or sections.get(
        "__DATA_CONST,__cfstring"
    )
    if not cfstr:
        return False
    file_off, _vm, size = cfstr
    n = size // 32
    for i in range(n):
        e = file_off + i * 32
        data_ptr = struct.unpack_from("<Q", data, e + 16)[0]
        if data_ptr == target_vmaddr:
            struct.pack_into("<Q", data, e + 24, new_len)
            return True
    return False


def _patch_cfstring_length(
    data: bytearray,
    sections: dict[str, tuple[int, int, int]],
    text: str,
    new_len: int,
    desc: str,
) -> int:
    """Locate `text` inside __cstring, then update its __cfstring entry's
    length field to `new_len`. Used to truncate https->http and zero
    fbapi / fb-messenger-share-api so canOpenURL: fails."""
    text_bytes = text.encode("utf-8")
    file_pos = _find_cstring_offset(data, sections, text_bytes)
    if file_pos < 0:
        print(f"  [!] SKIP {desc}: cstring {text!r} not found")
        return 0
    cstr = sections["__TEXT,__cstring"]
    cstr_file_off, cstr_vmaddr, _ = cstr
    target_vmaddr = cstr_vmaddr + (file_pos - cstr_file_off)
    if not _set_cfstring_length(data, sections, target_vmaddr, new_len):
        print(f"  [!] SKIP {desc}: no __cfstring entry references {text!r}")
        return 0
    print(f"  PATCHED {desc}: CFSTR {text!r} length -> {new_len}")
    return 1


def _patch_cfstring_text(
    data: bytearray,
    sections: dict[str, tuple[int, int, int]],
    text: str,
    new_text: str,
    desc: str,
    merge_with: str | None = None,
) -> int:
    """Overwrite __cstring bytes for `text` with `new_text` (NUL-padded)
    and update its __cfstring entry's length field to len(new_text).

    If `new_text` doesn't fit in the original `len(text)+1` slot but
    `merge_with` is supplied AND its cstring sits immediately after
    `text`'s NUL terminator, reuse the combined slot
    (len(text)+1+len(merge_with)+1 bytes; len(text)+len(merge_with)+1
    usable chars + a final NUL). The merge target's __cfstring length is
    zeroed so anything that still references it sees an empty NSString.
    """
    text_bytes = text.encode("utf-8")
    new_bytes = new_text.encode("utf-8")

    file_pos = _find_cstring_offset(data, sections, text_bytes)
    if file_pos < 0:
        print(f"  [!] SKIP {desc}: cstring {text!r} not found")
        return 0
    cstr = sections["__TEXT,__cstring"]
    cstr_file_off, cstr_vmaddr, _ = cstr
    text_vmaddr = cstr_vmaddr + (file_pos - cstr_file_off)

    slot = len(text_bytes) + 1
    merge_pos = -1
    merge_vmaddr = -1
    if merge_with is not None:
        merge_bytes = merge_with.encode("utf-8")
        candidate = file_pos + slot
        if data[candidate : candidate + len(merge_bytes) + 1] == merge_bytes + b"\x00":
            merge_pos = candidate
            merge_vmaddr = cstr_vmaddr + (merge_pos - cstr_file_off)
            slot += len(merge_bytes) + 1

    usable = slot - 1
    if len(new_bytes) > usable:
        print(
            f"  [!] SKIP {desc}: {new_text!r} too long for slot "
            f"({len(new_bytes)} > {usable} bytes)"
        )
        return 0

    data[file_pos : file_pos + slot] = new_bytes + b"\x00" * (slot - len(new_bytes))

    if not _set_cfstring_length(data, sections, text_vmaddr, len(new_bytes)):
        print(f"  [!] WARN {desc}: no __cfstring entry references {text!r}")

    if merge_pos > 0:
        _set_cfstring_length(data, sections, merge_vmaddr, 0)
        print(
            f"  PATCHED {desc}: {text!r}+{merge_with!r} slot ({slot}B) -> "
            f"{new_text!r} (CFSTR len {len(new_bytes)}; merge zeroed)"
        )
    else:
        print(
            f"  PATCHED {desc}: {text!r} ({slot}B) -> {new_text!r} "
            f"(CFSTR len {len(new_bytes)})"
        )
    return 1


# ----- ARM64 byte-pattern patches -----

# FBSDKCoreKit -[FBSDKInternalUtility _facebookURLWithHostPrefix:path:
#                                       queryParameters:defaultVersion:error:]
# Prologue snippet at file offset 0x4bc80 (5 instrs / 20 bytes):
#   mov x20, x5
#   mov x21, x4
#   mov x22, x3
#   str x0, [sp, #0x48]
#   mov x0, x2          <-- hostPrefix; we rewrite to mov x0, xzr
# The trailing instruction (last 4 bytes of the pattern) is the patch site.
HOSTPREFIX_PROLOGUE_PATTERN = bytes.fromhex(
    "f40305aaf50304aaf60303aae02700f9e00302aa"
)
HOSTPREFIX_PATCH_OFFSET_IN_PATTERN = 16
HOSTPREFIX_PATCH_INSTR = struct.pack("<I", 0xAA1F03E0)  # mov x0, xzr


def _patch_facebook_hostprefix_nil(data: bytearray) -> int:
    """Force the `hostPrefix` argument of -[FBSDKInternalUtility
    _facebookURLWithHostPrefix:...] to nil by rewriting one ARM64
    instruction in its prologue. Returns 1 on success."""
    pos = data.find(HOSTPREFIX_PROLOGUE_PATTERN)
    if pos < 0:
        print(
            "  [!] SKIP _facebookURLWithHostPrefix nil-out: prologue "
            "pattern not found (FBSDKCoreKit version mismatch?)"
        )
        return 0
    if data.find(HOSTPREFIX_PROLOGUE_PATTERN, pos + 1) >= 0:
        print(
            "  [!] SKIP _facebookURLWithHostPrefix nil-out: prologue "
            "pattern is not unique"
        )
        return 0
    site = pos + HOSTPREFIX_PATCH_OFFSET_IN_PATTERN
    orig = bytes(data[site : site + 4])
    data[site : site + 4] = HOSTPREFIX_PATCH_INSTR
    print(
        f"  PATCHED _facebookURLWithHostPrefix nil-out @ 0x{site:x}: "
        f"{orig.hex()} -> {HOSTPREFIX_PATCH_INSTR.hex()} (mov x0, xzr)"
    )
    return 1


# FBSDKLoginKit LoginManager.completeAuthentication validate-reauth gate.
# 12-byte / 3-instr pattern at file offset 0x2831c:
#   ldr x8, #0x3c64c        ; loads offset to dependencies.accessTokenWallet
#   ldr x8, [x0, x8]        ; -> AccessToken.current (or 0)
#   cbz x8, +0x80           ; if 0, skip to setGlobalProperties (we want this)
# Patch the trailing 4 bytes from `cbz x8, +0x80` (08 04 00 b4) to
# `b +0x80` (20 00 00 14) so the validate-reauth call is unconditionally
# skipped — required because a stale AccessToken.current on iOS makes
# new-user logins fail with userMismatch.
LOGINKIT_VALIDATE_PATTERN = bytes.fromhex("68321e58086868f8080400b4")
LOGINKIT_VALIDATE_PATCH_OFFSET_IN_PATTERN = 8
LOGINKIT_VALIDATE_PATCH_INSTR = struct.pack("<I", 0x14000020)  # b +0x80


def _patch_facebook_loginkit_skip_validate_reauth(data: bytearray) -> int:
    """Rewrite the `cbz x8, +0x80` in LoginManager.completeAuthentication
    to an unconditional branch so the iOS Facebook SDK always skips
    validateReauthentication. Returns 1 on success."""
    pos = data.find(LOGINKIT_VALIDATE_PATTERN)
    if pos < 0:
        print(
            "  [!] SKIP FBSDKLoginKit validateReauthentication bypass: "
            "pattern not found (FBSDKLoginKit version mismatch?)"
        )
        return 0
    if data.find(LOGINKIT_VALIDATE_PATTERN, pos + 1) >= 0:
        print(
            "  [!] SKIP FBSDKLoginKit validateReauthentication bypass: "
            "pattern is not unique"
        )
        return 0
    site = pos + LOGINKIT_VALIDATE_PATCH_OFFSET_IN_PATTERN
    orig = bytes(data[site : site + 4])
    data[site : site + 4] = LOGINKIT_VALIDATE_PATCH_INSTR
    print(
        f"  PATCHED FBSDKLoginKit validateReauthentication bypass @ "
        f"0x{site:x}: {orig.hex()} -> {LOGINKIT_VALIDATE_PATCH_INSTR.hex()} "
        f"(cbz x8 -> b +0x80)"
    )
    return 1


# FBSDKLoginKit LoginManager.applicationDidBecomeActive(_:) — root cause of
# "first attempt fails, second works". We RET-patch BOTH the Swift impl and
# the @objc thunk (what UIApplication notifications fire through the ObjC
# selector table) so neither can invoke handleImplicitCancelOfLogIn.
#
# Swift impl @ file offset 0x2c6e8 — 28-byte / 7-instr unique prologue:
#   sub sp, sp, #0x40 / stp x20,x19,[sp,#0x20] / stp x29,x30,[sp,#0x30] /
#   add x29,sp,#0x30 / nop / ldr x8,#0x38154 / add x19,x20,x8
LOGINKIT_DIDBECOMEACTIVE_SWIFT_PATTERN = bytes.fromhex(
    "ff0301d1f44f02a9fd7b03a9fdc300911f2003d5a80a1c589302088b"
)
# @objc thunk @ file offset 0x2c738 — 36-byte / 9-instr unique prologue:
#   sub sp,sp,#0x50 / stp x22,x21,[sp,#0x20] / stp x20,x19,[sp,#0x30] /
#   stp x29,x30,[sp,#0x40] / add x29,sp,#0x40 / mov x19,x2 / mov x20,x0 /
#   nop / ldr x8,#0x380f8
LOGINKIT_DIDBECOMEACTIVE_THUNK_PATTERN = bytes.fromhex(
    "ff4301d1f65702a9f44f03a9fd7b04a9fd030191f30302aaf40300aa1f2003d5c8071c58"
)
# Patch bytes: mov x0, #0 ; ret  (8 bytes; void function so x0 doesn't
# strictly need clearing but it's harmless and matches the existing style).
LOGINKIT_DIDBECOMEACTIVE_PATCH = (
    struct.pack("<I", 0xD2800000) + struct.pack("<I", 0xD65F03C0)
)


def _patch_facebook_loginkit_neuter_did_become_active(data: bytearray) -> int:
    """Write `mov x0, #0; ret` at the start of both the Swift impl and the
    @objc thunk for LoginManager.applicationDidBecomeActive(_:). Returns
    the number of sites patched (target: 2)."""
    n = 0
    for label, pattern in (
        ("Swift impl", LOGINKIT_DIDBECOMEACTIVE_SWIFT_PATTERN),
        ("@objc thunk", LOGINKIT_DIDBECOMEACTIVE_THUNK_PATTERN),
    ):
        pos = data.find(pattern)
        if pos < 0:
            print(
                f"  [!] SKIP applicationDidBecomeActive {label}: prologue "
                f"pattern not found (FBSDKLoginKit version mismatch?)"
            )
            continue
        if data.find(pattern, pos + 1) >= 0:
            print(
                f"  [!] SKIP applicationDidBecomeActive {label}: prologue "
                f"pattern is not unique"
            )
            continue
        orig = bytes(data[pos : pos + len(LOGINKIT_DIDBECOMEACTIVE_PATCH)])
        data[pos : pos + len(LOGINKIT_DIDBECOMEACTIVE_PATCH)] = (
            LOGINKIT_DIDBECOMEACTIVE_PATCH
        )
        print(
            f"  PATCHED applicationDidBecomeActive {label} @ 0x{pos:x}: "
            f"{orig.hex()} -> {LOGINKIT_DIDBECOMEACTIVE_PATCH.hex()} "
            f"(mov x0, #0; ret)"
        )
        n += 1
    return n


# FBSDKCoreKit -[FBSDKServerConfiguration useSafariViewControllerForDialogName:]
# and -[FBSDKServerConfigurationProvider useSafariViewControllerForDialogName:]
# — force plain Safari for OAuth.
#
# LoginManager.performBrowserLogIn (LoginManager.swift L629–680) gates the
# URL-opening path on serverConfigurationProvider.useSafariViewController-
# (forDialogName: "login"):
#   true  -> dependencies.urlOpener.openURLWithSafariViewController(...)
#            -> FBSDKBridgeAPI; if isAuthenticationURL: yes  -> ASWebAuth-
#            Session, else SFSafariViewController. Both flows route the
#            redirect through an in-process callback that on iOS 18+ no
#            longer fires reliably for our private LAN host (the system
#            privacy prompt the user keeps seeing is the giveaway).
#   false -> dependencies.urlOpener.open(url, ...) -> UIApplication.open(
#            url, options: @{}, completionHandler:) — i.e. plain Safari.
#            The 302 redirect to fb<appid>://authorize/?... bounces back
#            via iOS's standard application:openURL:options: which we
#            already wire through to FBSDKApplicationDelegate.
#
# Patching both selectors is belt-and-braces: LoginManager calls the
# Provider variant which forwards to the Configuration variant; either
# returning NO short-circuits the chain. We rely on the Mach-O symbol
# table (LC_SYMTAB) for IMP discovery — far more robust than scanning
# for a prologue byte pattern.
COREKIT_USE_SVC_SELECTORS = (
    "-[FBSDKServerConfiguration useSafariViewControllerForDialogName:]",
    "-[FBSDKServerConfigurationProvider useSafariViewControllerForDialogName:]",
)
# mov w0, #0 ; ret (8 bytes)
COREKIT_FORCE_PLAIN_SAFARI_PATCH = (
    struct.pack("<I", 0x52800000) + struct.pack("<I", 0xD65F03C0)
)


def _patch_facebook_corekit_force_plain_safari(
    data: bytearray, sections: dict[str, tuple[int, int, int]]
) -> int:
    """Overwrite the prologues of both -useSafariViewControllerForDialogName:
    IMPs in FBSDKCoreKit with `mov w0, #0; ret` so they always return NO.
    LoginManager then takes the plain-Safari URL-opener branch and the
    fb<appid>:// redirect comes back through application:openURL:options:.
    Returns the number of sites patched (target: 2)."""
    try:
        symbols = _parse_macho_symbols(data)
    except ValueError as e:
        print(f"  [!] SKIP force-plain-Safari: {e}")
        return 0
    n = 0
    for sym in COREKIT_USE_SVC_SELECTORS:
        vmaddr = symbols.get(sym)
        if vmaddr is None:
            print(f"  [!] SKIP force-plain-Safari: symbol not found: {sym}")
            continue
        site = _vmaddr_to_file_off(sections, vmaddr)
        if site < 0:
            print(
                f"  [!] SKIP force-plain-Safari: vmaddr 0x{vmaddr:x} for "
                f"{sym} outside known sections"
            )
            continue
        orig = bytes(data[site : site + len(COREKIT_FORCE_PLAIN_SAFARI_PATCH)])
        data[site : site + len(COREKIT_FORCE_PLAIN_SAFARI_PATCH)] = (
            COREKIT_FORCE_PLAIN_SAFARI_PATCH
        )
        print(
            f"  PATCHED force-plain-Safari {sym} @ 0x{site:x}: "
            f"{orig.hex()} -> {COREKIT_FORCE_PLAIN_SAFARI_PATCH.hex()} "
            f"(mov w0, #0; ret)"
        )
        n += 1
    return n


# ----- CFString neutralization list -----
# (text, new_length, description). Applied to FBSDKCoreKit only.
FBSDK_CFSTRING_PATCHES: list[tuple[str, int, str]] = [
    ("https", 4, "scheme: 'https' length 5 -> 4 (NSString reads 'http')"),
    ("fbapi", 0, "native FB SSO: zero canOpenURL: scheme 'fbapi'"),
    (
        "fb-messenger-share-api",
        0,
        "native Messenger SSO: zero canOpenURL: scheme 'fb-messenger-share-api'",
    ),
]


# ----- Orchestration -----

CORE_FW_REL = "Frameworks/FBSDKCoreKit.framework/FBSDKCoreKit"
LOGIN_FW_REL = "Frameworks/FBSDKLoginKit.framework/FBSDKLoginKit"
AEM_FW_REL = "Frameworks/FBAEMKit.framework/FBAEMKit"


def _patch_corekit(fw_path: str, auth_host: str) -> int:
    with open(fw_path, "rb") as f:
        data = bytearray(f.read())

    try:
        sections = _parse_macho_sections(data)
    except ValueError as e:
        print(f"  [!] FBSDKCoreKit: {e}")
        return 0

    n = 0
    n += _patch_cfstring_text(
        data,
        sections,
        text="facebook.com",
        new_text=auth_host,
        desc="FBSDKCoreKit base domain",
        merge_with=".facebook.com",
    )
    n += _patch_facebook_hostprefix_nil(data)
    n += _patch_facebook_corekit_force_plain_safari(data, sections)
    for text, new_len, desc in FBSDK_CFSTRING_PATCHES:
        n += _patch_cfstring_length(data, sections, text, new_len, desc)

    with open(fw_path, "wb") as f:
        f.write(data)
    return n


def _patch_loginkit(fw_path: str) -> int:
    with open(fw_path, "rb") as f:
        data = bytearray(f.read())

    n = 0
    n += _patch_facebook_loginkit_skip_validate_reauth(data)
    n += _patch_facebook_loginkit_neuter_did_become_active(data)

    with open(fw_path, "wb") as f:
        f.write(data)
    return n


def _patch_aemkit(fw_path: str, auth_host: str) -> int:
    """Replace the hardcoded `https://graph.facebook.com/v14.0/` with
    `http://<auth_host>/v14.0/`. AEM analytics failures are non-critical;
    if the new URL is too long for the slot we just skip."""
    old = b"https://graph.facebook.com/v14.0/"
    new = f"http://{auth_host}/v14.0/".encode("utf-8")

    with open(fw_path, "rb") as f:
        data = bytearray(f.read())

    pos = data.find(old)
    if pos < 0:
        print(f"  [!] NOT FOUND: FBAEMKit graph URL")
        return 0
    if len(new) > len(old):
        print(
            f"  [!] SKIP FBAEMKit graph URL: {new!r} too long for slot "
            f"({len(new)} > {len(old)} bytes); analytics will keep hitting "
            f"the original URL but login is unaffected"
        )
        return 0
    data[pos : pos + len(old)] = new + b"\x00" * (len(old) - len(new))
    with open(fw_path, "wb") as f:
        f.write(data)
    print(f"  PATCHED FBAEMKit graph URL: {old!r} -> {new!r}")
    return 1


def patch_facebook_frameworks(app_root: str, auth_host: str) -> int:
    total = 0

    core_path = os.path.join(app_root, CORE_FW_REL)
    if os.path.isfile(core_path):
        total += _patch_corekit(core_path, auth_host)
    else:
        print(f"  [!] NOT FOUND: {CORE_FW_REL}")

    login_path = os.path.join(app_root, LOGIN_FW_REL)
    if os.path.isfile(login_path):
        total += _patch_loginkit(login_path)
    else:
        print(f"  [!] NOT FOUND: {LOGIN_FW_REL}")

    aem_path = os.path.join(app_root, AEM_FW_REL)
    if os.path.isfile(aem_path):
        total += _patch_aemkit(aem_path, auth_host)
    else:
        print(f"  [!] NOT FOUND: {AEM_FW_REL}")

    return total


# ---------------------------------------------------------------------------
# UnityFramework Mach-O ARM64 binary patches
# ---------------------------------------------------------------------------

# ARM64 instruction encodings (little-endian)
MOV_X0_0 = struct.pack("<I", 0xD2800000)  # mov x0, #0
MOV_W0_1 = struct.pack("<I", 0x52800020)  # mov w0, #1
MOV_X0_X1 = struct.pack("<I", 0xAA0103E0)  # mov x0, x1
NOP = struct.pack("<I", 0xD503201F)  # nop
RET = struct.pack("<I", 0xD65F03C0)  # ret


def movz_w_imm16(rd: int, imm: int) -> bytes:
    """Encode ARM64 `MOVZ W<rd>, #imm` for a 16-bit immediate."""
    assert 0 <= rd <= 30, f"rd out of range: {rd}"
    assert 0 <= imm <= 0xFFFF, f"imm out of range: {imm}"
    return struct.pack("<I", 0x52800000 | (imm << 5) | rd)


# RVAs from dump.cs (Il2CppDumper output matching client/3.7.1.ipa).
# __TEXT has vmaddr=0 and fileoff=0, so dumper RVAs == file offsets in
# UnityFramework, same as the Android .so. iOS has two ToNativeCredentials
# implementations (Channel/Call), both patched.
IL2CPP_PATCHES = [
    {
        "name": "ToNativeCredentials (ChannelCredentialsExtensions)",
        "desc": "SSL bypass — return NULL to force insecure gRPC channel",
        "rva": 0x1A781F8,
        "bytes": MOV_X0_0 + RET,
    },
    {
        "name": "ToNativeCredentials (CallCredentialsExtensions)",
        "desc": "SSL bypass — return NULL for per-call credentials as well",
        "rva": 0x1A7D8F8,
        "bytes": MOV_X0_0 + RET,
    },
    {
        "name": "HandleNet.Encrypt",
        "desc": "encryption passthrough — return payload as-is",
        "rva": 0x1C9869C,
        "bytes": MOV_X0_X1 + RET,
    },
    {
        "name": "HandleNet.Decrypt",
        "desc": "decryption passthrough — return receivedMessage as-is",
        "rva": 0x1C98794,
        "bytes": MOV_X0_X1 + RET,
    },
    {
        "name": "Octo.Loader.OctoAPI.GetListAes",
        "desc": "Octo list — force plain list (return false = no AES); server serves raw list.bin",
        "rva": 0x3E6DA8C,
        "bytes": MOV_X0_0 + RET,
    },
    {
        "name": "PurchaseRealProductAsync.MoveNext (inlined IsInitialized check)",
        "desc": "IAP bypass — NOP the cbz W9 that branches to PurchasingUnavailable",
        "rva": 0x1C3B5BC,
        "bytes": NOP,
    },
    {
        "name": "PurchaseRealProductAsync.MoveNext (skip CheckPurchasingAlert)",
        "desc": "fast purchase — B past CheckPurchasingAlert/CESA dialog to post-alert code",
        "rva": 0x1C3B5C0,
        "bytes": struct.pack("<I", 0x14000017),
    },
    {
        "name": "Purchaser.IsExistProduct",
        "desc": "IAP bypass — always report product as existing in store",
        "rva": 0x1C36800,
        "bytes": MOV_W0_1 + RET,
    },
    {
        "name": "Purchaser.<BuyProduct>d__24.MoveNext (null _storeController)",
        "desc": "IAP bypass — redirect null _storeController CBZ to cancelled-return path",
        "rva": 0x1C3D9E8,
        "bytes": struct.pack("<I", 0xB4001475),
    },
    # NOTE: do not patch DarkPurchase.<Initialize>d__16.MoveNext on iOS.
    # On Android, the equivalent patch lands inside Purchaser.<Initialize>d__24
    # (Google Play init). On iOS, the corresponding state machine is
    # Purchaser.<InitializeIos>d__22 (RVA 0x1C3E430) — DarkPurchase.<Initialize>d__16
    # is just the outer wrapper. NOPing the cbz here makes Initialize a no-op
    # for the default isForce=false call path, which leaves _initialized=false
    # and downstream callers (title boot) hang waiting for it.
    {
        "name": "TitleScreen.InitializeMenuButton",
        "desc": "EOS bypass — skip kHideMenuButtonUnixTime check that hides menu button after service end",
        "rva": 0x1F1BCD0,
        "bytes": RET,
    },
]


def patch_unity_framework(fw_path: str) -> int:
    with open(fw_path, "r+b") as f:
        file_size = f.seek(0, 2)
        patched = 0
        for p in IL2CPP_PATCHES:
            rva = p["rva"]
            if rva + len(p["bytes"]) > file_size:
                print(
                    f"  [!] SKIP {p['name']}: RVA 0x{rva:X} beyond file size 0x{file_size:X}"
                )
                continue

            f.seek(rva)
            orig = f.read(len(p["bytes"]))

            f.seek(rva)
            f.write(p["bytes"])
            patched += 1
            print(f"  {p['name']} @ 0x{rva:X}: {orig.hex()} -> {p['bytes'].hex()}")
            print(f"    {p['desc']}")

    return patched


# ---------------------------------------------------------------------------
# IPA unpack / repack
# ---------------------------------------------------------------------------


def unpack_ipa(ipa_path: str, dest_dir: str) -> None:
    with zipfile.ZipFile(ipa_path, "r") as z:
        z.extractall(dest_dir)


def repack_ipa(src_dir: str, out_path: str) -> None:
    """Repack a directory tree into an IPA (ZIP with no compression for binaries,
    deflate for everything else). Preserves Unix permission bits."""
    with zipfile.ZipFile(out_path, "w", allowZip64=True) as zf:
        for root, dirs, files in os.walk(src_dir):
            dirs.sort()
            for fname in sorted(files):
                abs_path = os.path.join(root, fname)
                arc_name = os.path.relpath(abs_path, src_dir)

                st = os.stat(abs_path)
                info = zipfile.ZipInfo(arc_name)
                info.external_attr = (st.st_mode & 0xFFFF) << 16

                ext = os.path.splitext(fname)[1].lower()
                binary_exts = {".dylib", ""}  # no extension = Mach-O executables
                if ext in binary_exts and st.st_size > 4096:
                    info.compress_type = zipfile.ZIP_STORED
                else:
                    info.compress_type = zipfile.ZIP_DEFLATED

                with open(abs_path, "rb") as f:
                    zf.writestr(info, f.read())


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(
        description="Patch NieR Re[in]carnation IPA for private server"
    )
    p.add_argument("ipa", help="Path to the original IPA (e.g. tumgb4.ipa)")
    p.add_argument(
        "--grpc-addr",
        required=True,
        help="gRPC game server address as host:port (e.g. 192.168.1.100:7777); "
        "use port 443 to skip dynamic port patches",
    )
    p.add_argument(
        "--http-addr",
        required=True,
        help="HTTP/CDN address as host:port (e.g. 192.168.1.100:8080)",
    )
    p.add_argument(
        "--auth-host",
        help="Auth server host (and optional :port) for Facebook OAuth "
        "redirect (e.g. 192.168.1.100:3000). The framework patcher reuses "
        "the adjacent '.facebook.com' cstring slot, giving up to 25 "
        "characters; the IL2CPP metadata literal still has a 12-char "
        "budget but is non-essential when the framework patches succeed.",
    )
    p.add_argument(
        "--output",
        "-o",
        default="patched.ipa",
        help="Output IPA path (default: patched.ipa)",
    )
    args = p.parse_args()

    auth_host = args.auth_host
    if auth_host and len(auth_host.encode("utf-8")) > 25:
        sys.exit(
            f"[!] --auth-host {auth_host!r} is {len(auth_host)} bytes; the "
            f"merged FBSDKCoreKit cstring slot allows at most 25 bytes. "
            f"Use a shorter host:port (e.g. drop the port and run the auth "
            f"server on :80, or use a shorter IP/hostname)."
        )

    grpc_host, grpc_port_str = args.grpc_addr.rsplit(":", 1)
    try:
        gp = int(grpc_port_str)
    except ValueError:
        sys.exit(f"[!] Invalid gRPC port in --grpc-addr: {grpc_port_str!r}")
    if not (1 <= gp <= 65535):
        sys.exit(f"[!] gRPC port must be 1..65535, got {gp}")

    web_url = f"http://{args.http_addr}"

    if not os.path.isfile(args.ipa):
        sys.exit(f"[!] IPA not found: {args.ipa}")

    # The metadata replacement for api.app.nierreincarnation.com uses host only —
    # the client appends _serverPort separately; including a port here would
    # produce "ip:port:port" when the channel is created.
    if gp != 443:
        IL2CPP_PATCHES.append(
            {
                "name": "NetworkConfig.get_ServerPort",
                "desc": f"gRPC port override — return {gp} instead of serialized 443",
                "rva": 0x1C22D54,
                "bytes": movz_w_imm16(0, gp) + RET,
            }
        )
        IL2CPP_PATCHES.append(
            {
                "name": "InitializeApiClient.OnStateBegin (port override)",
                "desc": f"conductor port override — MOVZ W2, #{gp} before InitializeApiClient call",
                # +76 from function entry: replaces MOV X2, X0 (AA0003E2)
                "rva": 0x289DFF4,
                "bytes": movz_w_imm16(2, gp),
            }
        )
        IL2CPP_PATCHES.append(
            {
                "name": "CalculatorNetworking.InitializeApiClient (port override)",
                "desc": f"catch-all port override — MOVZ W20, #{gp} replaces saved port param",
                # +24 from function entry: replaces MOV X20, X2 (AA0203F4)
                "rva": 0x1C1A8F4,
                "bytes": movz_w_imm16(20, gp),
            }
        )

    replacements = [
        ("api.app.nierreincarnation.com", grpc_host),
        (
            "https://web.app.nierreincarnation.com/assets/release/{0}/database.bin",
            f"{web_url}/assets/release/{{0}}/database.bin",
        ),
        ("https://web.app.nierreincarnation.com", web_url),
        ("https://resources-api.app.nierreincarnation.com/", f"{web_url}/"),
    ]

    if auth_host:
        old_fb = "facebook.com"
        if len(auth_host.encode("utf-8")) > len(old_fb.encode("utf-8")):
            print(
                f"  [!] NOTE: --auth-host {auth_host!r} longer than "
                f"'facebook.com' ({len(auth_host)} > 12) — IL2CPP metadata "
                f"Facebook literal will be skipped; framework patches use "
                f"the merged 'facebook.com\\0.facebook.com\\0' slot (up to "
                f"25 chars) and are sufficient for OAuth redirection."
            )
        else:
            replacements.append((old_fb, auth_host))

    for old, new in replacements:
        if len(new.encode()) > len(old.encode()):
            sys.exit(
                f"[!] Replacement too long ({len(new)} > {len(old)}): "
                f"{old!r} -> {new!r}\n"
                f"    Use a shorter server address or omit the port for port 80."
            )

    print(f"\n[*] Patching for gRPC={args.grpc_addr}, HTTP={args.http_addr}")
    print(f"    web URL   = {web_url}")
    print(f"    gRPC host = {grpc_host}:{gp}")
    if auth_host:
        print(f"    auth host = {auth_host} (Facebook OAuth redirect)")
    else:
        print("    auth host = (none, Facebook login patching skipped)")

    tmp = tempfile.mkdtemp(prefix="nier_ipa_")
    try:
        print(f"\n[*] Unpacking IPA to {tmp} ...")
        unpack_ipa(args.ipa, tmp)

        app_root = os.path.join(tmp, "Payload", "NieR.app")
        meta = os.path.join(
            app_root, "Data", "Managed", "Metadata", "global-metadata.dat"
        )
        fw = os.path.join(
            app_root, "Frameworks", "UnityFramework.framework", "UnityFramework"
        )
        info_plist = os.path.join(app_root, "Info.plist")

        for path in (meta, fw, info_plist):
            if not os.path.isfile(path):
                sys.exit(f"[!] Expected file not found: {path}")

        print(f"\n[1] Patching global-metadata.dat string literals ...")
        n = patch_metadata_strings(meta, replacements)
        print(f"    {n}/{len(replacements)} strings patched")

        print(f"\n[2] Patching Info.plist ...")
        patch_info_plist(info_plist)

        if auth_host:
            print(
                f"\n[3] Patching Facebook SDK frameworks "
                f"(base domain + hostprefix nil + force plain Safari + "
                f"https->http + native-SSO neuter + validateReauth bypass "
                f"+ didBecomeActive neuter + AEM graph URL) ..."
            )
            n3 = patch_facebook_frameworks(app_root, auth_host)
            print(f"    {n3} framework patches applied")

        print(
            f"\n[4] Patching UnityFramework "
            f"(SSL bypass + encryption passthrough + IAP bypass + EOS bypass) ..."
        )
        n2 = patch_unity_framework(fw)
        print(f"    {n2}/{len(IL2CPP_PATCHES)} methods patched")

        print(f"\n[*] Repacking IPA -> {args.output} ...")
        repack_ipa(tmp, args.output)
        size_mb = os.path.getsize(args.output) / 1024 / 1024
        print(f"    wrote {args.output} ({size_mb:.1f} MB)")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n[+] Done. Re-sign before installing:")
    print(f"    AltStore / Sideloadly: import {args.output} (auto-resigns)")
    print(f"    ldid (jailbroken):     ldid -S {args.output}")
    print(f"    codesign (macOS):      unzip {args.output} -d patched_dir &&")
    print(
        f"                           codesign -f -s 'iPhone Developer: NAME' "
        f"patched_dir/Payload/NieR.app &&"
    )
    print(f"                           cd patched_dir && zip -r ../{args.output} Payload")


if __name__ == "__main__":
    main()
