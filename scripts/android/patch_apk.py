#!/usr/bin/env python3
"""
patch_apk.py — Static patcher for NieR Re[in]carnation APK.

Patches an apktool-decompiled APK directory so the game connects to a
private server without any runtime (Frida) hooks.

Patches applied:
  1. global-metadata.dat  — rewrite IL2CPP string literals (URLs + hostname)
  2. libil2cpp.so          — ARM64 binary patches (SSL bypass, encryption passthrough,
                             Octo plain list, Google Play billing bypass)
  3. AndroidManifest.xml  — add networkSecurityConfig for cleartext HTTP
  4. res/xml/network_security_config.xml — allow cleartext traffic
  5. smali (DEX)           — redirect Facebook SDK OAuth to custom auth server
                             via Chrome Custom Tabs (--auth-host):
                              - rewrite domain/format strings in com/facebook/**
                              - force CustomTabUtils.getValidRedirectURI() to
                                always return fbconnect://cct.<pkg>, bypassing
                                the FB SDK's "another app is also listening"
                                security check (broken on devices with the FB
                                app or Lite installed)
                              - flip LoginBehavior.NATIVE_WITH_FALLBACK so
                                it permits ONLY the in-app WebView path
                                (Katana, CCT, Facebook Lite, and Instagram
                                SSO are all disabled). Chrome 117+ silently
                                drops app-launched HTTP-to-private-IP
                                navigations to about:blank in both CCT and
                                regular tabs (HTTPS-Upgrade / Private
                                Network Access), but WebView is governed
                                by the app's own network_security_config
                                and is exempt.
                              - add the API-24 shouldOverrideUrlLoading
                                (WebView, WebResourceRequest) override to
                                WebDialog$DialogWebViewClient so Chromium
                                WebView 75+ actually delivers the
                                fbconnect:// cross-scheme navigation to
                                the SDK (forwards to the existing
                                deprecated string-based overload).
"""

import argparse
import os
import struct
import sys

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
# AndroidManifest.xml  — add networkSecurityConfig attribute
# ---------------------------------------------------------------------------


def patch_manifest(manifest_path: str) -> bool:
    with open(manifest_path, "r", encoding="utf-8") as f:
        text = f.read()

    changed = False

    if "networkSecurityConfig" not in text:
        new_attr = 'android:networkSecurityConfig="@xml/network_security_config"'
        text = text.replace("<application ", f"<application {new_attr} ", 1)
        print(f"  added {new_attr}")
        changed = True
    else:
        print("  already has networkSecurityConfig")

    if 'android:extractNativeLibs="false"' in text:
        text = text.replace(
            'android:extractNativeLibs="false"', 'android:extractNativeLibs="true"'
        )
        print("  extractNativeLibs: false -> true")
        changed = True

    if changed:
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(text)

    return True


# ---------------------------------------------------------------------------
# res/xml/network_security_config.xml
# ---------------------------------------------------------------------------

NETWORK_SECURITY_CONFIG = """\
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true" />
</network-security-config>
"""


def create_network_security_config(res_xml_dir: str) -> bool:
    os.makedirs(res_xml_dir, exist_ok=True)
    out = os.path.join(res_xml_dir, "network_security_config.xml")
    with open(out, "w", encoding="utf-8") as f:
        f.write(NETWORK_SECURITY_CONFIG)
    print(f"  wrote {out}")
    return True


# ---------------------------------------------------------------------------
# smali (DEX) Facebook SDK patching — redirect OAuth to custom auth server
# ---------------------------------------------------------------------------

# The Facebook Android SDK constructs URLs from a base domain + format strings:
#   FacebookSdk.facebookDomain = "facebook.com"
#   ServerProtocol.DIALOG_AUTHORITY_FORMAT  = "m.%s"      -> "m.facebook.com"
#   ServerProtocol.GRAPH_URL_FORMAT         = "https://graph.%s"  -> "https://graph.facebook.com"
#   ServerProtocol.GRAPH_VIDEO_URL_FORMAT   = "https://graph-video.%s"
#
# We patch both the base domain and the format strings so the SDK builds
# URLs pointing at our auth server instead.

SMALI_REPLACEMENTS: list[tuple[str, str]] = [
    # Format strings in ServerProtocol.smali — strip subdomain prefixes
    ('"m.%s"', '"%s"'),
    ('"https://graph.%s"', '"http://%s"'),
    ('"https://graph-video.%s"', '"http://%s"'),
    # Utility.URL_SCHEME — buildUri() hardcodes "https"; downgrade to "http"
    ('"https"', '"http"'),
    # Disable native Facebook app SSO — belt-and-suspenders alongside
    # force_webview_only_login_behavior() (which already drops Katana from
    # NATIVE_WITH_FALLBACK at the LoginBehavior layer). Renaming the package
    # IDs prevents Validate.hasFacebookActivity / Validate.hasInternet
    # PackageManager probes from short-circuiting if the user has the FB
    # app installed.
    ('"com.facebook.katana"', '"com.disabled.katana"'),
    ('"com.facebook.orca"', '"com.disabled.orca"'),
    # Hardcoded full domains (belt-and-suspenders)
    ('"www.facebook.com"', None),  # filled at runtime with auth_host
    ('"graph.facebook.com"', None),
]

# Base domain — replaced separately since it appears in FacebookSdk.smali
SMALI_BASE_DOMAIN = ('"facebook.com"', None)  # filled at runtime


def patch_facebook_smali(apk_dir: str, auth_host: str) -> int:
    """Walk smali/com/facebook/ and rewrite Facebook domain strings."""

    replacements = []
    for old, new in SMALI_REPLACEMENTS:
        if new is None:
            new = f'"{auth_host}"'
        replacements.append((old, new))
    replacements.append((SMALI_BASE_DOMAIN[0], f'"{auth_host}"'))

    fb_dirs = []
    for entry in os.listdir(apk_dir):
        if not entry.startswith("smali"):
            continue
        fb_path = os.path.join(apk_dir, entry, "com", "facebook")
        if os.path.isdir(fb_path):
            fb_dirs.append(fb_path)

    if not fb_dirs:
        print("  [!] No smali/com/facebook/ directories found")
        return 0

    files_patched = 0
    for fb_dir in fb_dirs:
        for dirpath, _, filenames in os.walk(fb_dir):
            for fname in filenames:
                if not fname.endswith(".smali"):
                    continue
                fpath = os.path.join(dirpath, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()

                new_content = content
                for old, new in replacements:
                    new_content = new_content.replace(old, new)

                if new_content != content:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    rel = os.path.relpath(fpath, apk_dir)
                    print(f"  PATCHED: {rel}")
                    files_patched += 1

    return files_patched


def _find_smali(apk_dir: str, rel_path: str) -> str | None:
    """Find rel_path under any smali*/ root in apk_dir."""
    for entry in os.listdir(apk_dir):
        if not entry.startswith("smali"):
            continue
        candidate = os.path.join(apk_dir, entry, *rel_path.split("/"))
        if os.path.isfile(candidate):
            return candidate
    return None


def bypass_cct_redirect_check(apk_dir: str) -> bool:
    """Force CustomTabUtils.getValidRedirectURI() to always return
    getDefaultRedirectURI() ("fbconnect://cct.<pkg>"). The FB SDK normally
    queries the device's PackageManager and bails to "" if any *other* app
    is also listening for the fbconnect URI scheme — which is the case on
    most devices that have the official Facebook app or Lite installed.
    Bypassing that gate lets our own com.facebook.CustomTabActivity (declared
    in AndroidManifest.xml for fbconnect://cct.<pkg>) handle the 302."""
    target = _find_smali(apk_dir, "com/facebook/internal/CustomTabUtils.smali")
    if not target:
        print("  [!] CustomTabUtils.smali not found")
        return False
    with open(target, "r", encoding="utf-8") as f:
        text = f.read()
    needle = (
        "    :cond_0\n"
        "    :try_start_0\n"
        '    const-string v1, "developerDefinedRedirectURI"\n'
    )
    replacement = (
        "    :cond_0\n"
        "    invoke-static {}, Lcom/facebook/internal/CustomTabUtils;->"
        "getDefaultRedirectURI()Ljava/lang/String;\n"
        "\n"
        "    move-result-object p0\n"
        "\n"
        "    return-object p0\n"
        "\n"
        "    :try_start_0\n"
        '    const-string v1, "developerDefinedRedirectURI"\n'
    )
    if needle not in text:
        print("  [!] getValidRedirectURI marker not found (already patched?)")
        return False
    new_text = text.replace(needle, replacement, 1)
    with open(target, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(
        f"  PATCHED: {os.path.relpath(target, apk_dir)} "
        f"(getValidRedirectURI -> always getDefaultRedirectURI)"
    )
    return True


def patch_customtab_use_plain_view_intent(apk_dir: str) -> bool:
    """Replace CustomTab.openCustomTab's body so it launches the OAuth URL with
    a plain Intent.ACTION_VIEW (regular Chrome tab) instead of CustomTabsIntent.
    launchUrl. This mirrors what the iOS patcher does (forces plain Safari
    instead of ASWebAuthenticationSession / SFSafariViewController) and side-
    steps Chrome CCT's HTTPS-Upgrade / Private Network Access policy that
    silently drops http:// navigations to private IPs from third-party apps
    to about:blank. The OAuth 302 to fbconnect://cct.<pkg> still routes back
    to com.facebook.CustomTabActivity via the existing BROWSABLE intent
    filter in AndroidManifest.xml — same return path as CCT, just opened
    with a regular tab."""
    target = _find_smali(apk_dir, "com/facebook/internal/CustomTab.smali")
    if not target:
        print("  [!] CustomTab.smali not found")
        return False
    with open(target, "r", encoding="utf-8") as f:
        text = f.read()

    needle = (
        ".method public final openCustomTab(Landroid/app/Activity;Ljava/lang/String;)Z\n"
        "    .locals 3\n"
        "\n"
        "    invoke-static {p0}, Lcom/facebook/internal/instrument/crashshield/CrashShieldHandler;->isObjectCrashing(Ljava/lang/Object;)Z\n"
        "\n"
        "    move-result v0\n"
        "\n"
        "    const/4 v1, 0x0\n"
        "\n"
        "    if-eqz v0, :cond_0\n"
        "\n"
        "    return v1\n"
        "\n"
        "    :cond_0\n"
        "    :try_start_0\n"
        "    const-string v0, \"activity\"\n"
        "\n"
        "    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V\n"
        "\n"
        "    .line 38\n"
        "    sget-object v0, Lcom/facebook/login/CustomTabPrefetchHelper;->Companion:Lcom/facebook/login/CustomTabPrefetchHelper$Companion;\n"
        "\n"
        "    invoke-virtual {v0}, Lcom/facebook/login/CustomTabPrefetchHelper$Companion;->getPreparedSessionOnce()Landroidx/browser/customtabs/CustomTabsSession;\n"
        "\n"
        "    move-result-object v0\n"
        "\n"
        "    .line 39\n"
        "    new-instance v2, Landroidx/browser/customtabs/CustomTabsIntent$Builder;\n"
        "\n"
        "    invoke-direct {v2, v0}, Landroidx/browser/customtabs/CustomTabsIntent$Builder;-><init>(Landroidx/browser/customtabs/CustomTabsSession;)V\n"
        "\n"
        "    invoke-virtual {v2}, Landroidx/browser/customtabs/CustomTabsIntent$Builder;->build()Landroidx/browser/customtabs/CustomTabsIntent;\n"
        "\n"
        "    move-result-object v0\n"
        "\n"
        "    .line 40\n"
        "    iget-object v2, v0, Landroidx/browser/customtabs/CustomTabsIntent;->intent:Landroid/content/Intent;\n"
        "\n"
        "    invoke-virtual {v2, p2}, Landroid/content/Intent;->setPackage(Ljava/lang/String;)Landroid/content/Intent;\n"
        "    :try_end_0\n"
        "    .catchall {:try_start_0 .. :try_end_0} :catchall_0\n"
        "\n"
        "    .line 42\n"
        "    :try_start_1\n"
        "    check-cast p1, Landroid/content/Context;\n"
        "\n"
        "    iget-object p2, p0, Lcom/facebook/internal/CustomTab;->uri:Landroid/net/Uri;\n"
        "\n"
        "    invoke-virtual {v0, p1, p2}, Landroidx/browser/customtabs/CustomTabsIntent;->launchUrl(Landroid/content/Context;Landroid/net/Uri;)V\n"
        "    :try_end_1\n"
        "    .catch Landroid/content/ActivityNotFoundException; {:try_start_1 .. :try_end_1} :catch_0\n"
        "    .catchall {:try_start_1 .. :try_end_1} :catchall_0\n"
        "\n"
        "    const/4 p1, 0x1\n"
        "\n"
        "    return p1\n"
        "\n"
        "    :catch_0\n"
        "    return v1\n"
        "\n"
        "    :catchall_0\n"
        "    move-exception p1\n"
        "\n"
        "    .line 46\n"
        "    invoke-static {p1, p0}, Lcom/facebook/internal/instrument/crashshield/CrashShieldHandler;->handleThrowable(Ljava/lang/Throwable;Ljava/lang/Object;)V\n"
        "\n"
        "    return v1\n"
        ".end method\n"
    )
    replacement = (
        ".method public final openCustomTab(Landroid/app/Activity;Ljava/lang/String;)Z\n"
        "    .locals 4\n"
        "\n"
        "    invoke-static {p0}, Lcom/facebook/internal/instrument/crashshield/CrashShieldHandler;->isObjectCrashing(Ljava/lang/Object;)Z\n"
        "\n"
        "    move-result v0\n"
        "\n"
        "    const/4 v1, 0x0\n"
        "\n"
        "    if-eqz v0, :cond_0\n"
        "\n"
        "    return v1\n"
        "\n"
        "    :cond_0\n"
        "    const-string v0, \"activity\"\n"
        "\n"
        "    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V\n"
        "\n"
        "    :try_start_0\n"
        "    new-instance v2, Landroid/content/Intent;\n"
        "\n"
        "    const-string v3, \"android.intent.action.VIEW\"\n"
        "\n"
        "    iget-object v0, p0, Lcom/facebook/internal/CustomTab;->uri:Landroid/net/Uri;\n"
        "\n"
        "    invoke-direct {v2, v3, v0}, Landroid/content/Intent;-><init>(Ljava/lang/String;Landroid/net/Uri;)V\n"
        "\n"
        "    invoke-virtual {v2, p2}, Landroid/content/Intent;->setPackage(Ljava/lang/String;)Landroid/content/Intent;\n"
        "\n"
        "    invoke-virtual {p1, v2}, Landroid/app/Activity;->startActivity(Landroid/content/Intent;)V\n"
        "    :try_end_0\n"
        "    .catch Landroid/content/ActivityNotFoundException; {:try_start_0 .. :try_end_0} :catch_0\n"
        "    .catchall {:try_start_0 .. :try_end_0} :catchall_0\n"
        "\n"
        "    const/4 p1, 0x1\n"
        "\n"
        "    return p1\n"
        "\n"
        "    :catch_0\n"
        "    return v1\n"
        "\n"
        "    :catchall_0\n"
        "    move-exception p1\n"
        "\n"
        "    invoke-static {p1, p0}, Lcom/facebook/internal/instrument/crashshield/CrashShieldHandler;->handleThrowable(Ljava/lang/Throwable;Ljava/lang/Object;)V\n"
        "\n"
        "    return v1\n"
        ".end method\n"
    )
    if needle not in text:
        print(
            "  [!] openCustomTab body marker not found "
            "(already patched, or CustomTab.smali shape changed?)"
        )
        return False
    new_text = text.replace(needle, replacement, 1)
    with open(target, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(
        f"  PATCHED: {os.path.relpath(target, apk_dir)} "
        f"(openCustomTab -> plain Intent.ACTION_VIEW startActivity)"
    )
    return True


def force_webview_only_login_behavior(apk_dir: str) -> bool:
    """Flip LoginBehavior.NATIVE_WITH_FALLBACK so it permits ONLY in-app
    WebView. We disable Katana (FB app SSO), Chrome Custom Tabs, Facebook
    Lite SSO, and Instagram SSO. CCT and a regular Intent.ACTION_VIEW
    Chrome tab both silently drop app-launched HTTP-to-private-IP
    navigations to about:blank under Chrome 117+'s HTTPS-Upgrade and
    Private Network Access policies (the user verified disabling
    chrome://flags/#https-upgrades was insufficient). WebView is governed
    by the app's own network_security_config.xml (cleartextTrafficPermitted
    is already true) and is exempt from those Chromium policies, so it
    reliably loads http://<auth-host>/v14.0/dialog/oauth and intercepts
    the fbconnect:// 302 in shouldOverrideUrlLoading.

    Constructor signature is (Ljava/lang/String;IZZZZZZZ)V where the
    booleans (in order) are: allowsGetTokenAuth (p3/v3),
    allowsKatanaAuth (p4/v4), allowsWebViewAuth (p5/v5),
    allowsDeviceAuth (p6/v6), allowsCustomTabAuth (p7/v7),
    allowsFacebookLiteAuth (p8/v8), allowsInstagramAppAuth (p9/v9).
    Fresh-decompile NATIVE_WITH_FALLBACK is (1,1,1,0,1,1,1); we flip
    v4, v7, v8, v9 to 0 and leave v3 and v5 at 1."""
    target = _find_smali(apk_dir, "com/facebook/login/LoginBehavior.smali")
    if not target:
        print("  [!] LoginBehavior.smali not found")
        return False
    with open(target, "r", encoding="utf-8") as f:
        text = f.read()
    needle = '    const-string v1, "NATIVE_WITH_FALLBACK"'
    start = text.find(needle)
    end = text.find("invoke-direct/range", start) if start >= 0 else -1
    if start < 0 or end < 0:
        print("  [!] NATIVE_WITH_FALLBACK enum-constant block not found")
        return False
    block = text[start:end]
    flips = [
        ("const/4 v4, 0x1", "const/4 v4, 0x0"),  # allowsKatanaAuth = false
        ("const/4 v7, 0x1", "const/4 v7, 0x0"),  # allowsCustomTabAuth = false
        ("const/4 v8, 0x1", "const/4 v8, 0x0"),  # allowsFacebookLiteAuth = false
        ("const/4 v9, 0x1", "const/4 v9, 0x0"),  # allowsInstagramAppAuth = false
    ]
    new_block = block
    applied = []
    for old, new in flips:
        if old in new_block:
            new_block = new_block.replace(old, new, 1)
            applied.append(old.split(", ")[0].split()[-1])  # e.g. "v4"
    if new_block == block:
        print(
            "  [!] no flips applied — NATIVE_WITH_FALLBACK already patched "
            "or layout shifted"
        )
        return False
    with open(target, "w", encoding="utf-8") as f:
        f.write(text[:start] + new_block + text[end:])
    print(
        f"  PATCHED: {os.path.relpath(target, apk_dir)} "
        f"(NATIVE_WITH_FALLBACK -> WebView-only; flipped {', '.join(applied)})"
    )
    return True


def patch_webdialog_request_overload(apk_dir: str) -> bool:
    """Add the API-24 shouldOverrideUrlLoading(WebView, WebResourceRequest)
    override to the FB SDK's WebDialog$DialogWebViewClient. The SDK only
    overrides the deprecated string-based overload, but Chromium WebView
    75+ silently drops cross-scheme navigations (e.g. fbconnect://success)
    when the API-24 overload is absent — the deprecated overload is never
    reached, the SDK never extracts access_token from the URL fragment,
    and LoginManager's success callback never fires. The new overload
    extracts the URL via WebResourceRequest.getUrl().toString() and
    forwards to the existing string-based override on the same instance,
    so all of the SDK's existing fbconnect handling logic runs unchanged."""
    target = _find_smali(
        apk_dir, "com/facebook/internal/WebDialog$DialogWebViewClient.smali"
    )
    if not target:
        print("  [!] WebDialog$DialogWebViewClient.smali not found")
        return False
    with open(target, "r", encoding="utf-8") as f:
        text = f.read()
    sig = (
        "shouldOverrideUrlLoading(Landroid/webkit/WebView;"
        "Landroid/webkit/WebResourceRequest;)Z"
    )
    if sig in text:
        print(f"  [!] {os.path.relpath(target, apk_dir)} already has API-24 overload")
        return False
    new_method = (
        "\n"
        ".method public shouldOverrideUrlLoading(Landroid/webkit/WebView;"
        "Landroid/webkit/WebResourceRequest;)Z\n"
        "    .locals 1\n"
        "\n"
        "    invoke-interface {p2}, Landroid/webkit/WebResourceRequest;->"
        "getUrl()Landroid/net/Uri;\n"
        "\n"
        "    move-result-object v0\n"
        "\n"
        "    invoke-virtual {v0}, Landroid/net/Uri;->toString()Ljava/lang/String;\n"
        "\n"
        "    move-result-object v0\n"
        "\n"
        "    invoke-virtual {p0, p1, v0}, "
        "Lcom/facebook/internal/WebDialog$DialogWebViewClient;->"
        "shouldOverrideUrlLoading(Landroid/webkit/WebView;Ljava/lang/String;)Z\n"
        "\n"
        "    move-result v0\n"
        "\n"
        "    return v0\n"
        ".end method\n"
    )
    if not text.endswith("\n"):
        text += "\n"
    new_text = text + new_method
    with open(target, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(
        f"  PATCHED: {os.path.relpath(target, apk_dir)} "
        f"(added API-24 shouldOverrideUrlLoading WebResourceRequest overload)"
    )
    return True


# ---------------------------------------------------------------------------
# libil2cpp.so ARM64 binary patches
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


# RVAs from dump.cs (Il2CppDumper output matching client/3.7.1.apk)
IL2CPP_PATCHES = [
    {
        "name": "ToNativeCredentials",
        "desc": "SSL bypass — return NULL to force insecure gRPC channel",
        "rva": 0x35C8670,
        "bytes": MOV_X0_0 + RET,
    },
    {
        "name": "HandleNet.Encrypt",
        "desc": "encryption passthrough — return payload as-is",
        "rva": 0x279410C,
        "bytes": MOV_X0_X1 + RET,
    },
    {
        "name": "HandleNet.Decrypt",
        "desc": "decryption passthrough — return receivedMessage as-is",
        "rva": 0x279420C,
        "bytes": MOV_X0_X1 + RET,
    },
    {
        "name": "OctoManager.Internal.GetListAes",
        "desc": "Octo list: force plain list (return false = no AES); server serves raw list.bin",
        "rva": 0x4C27038,
        "bytes": MOV_X0_0 + RET,
    },
    {
        "name": "PurchaseRealProductAsync.MoveNext (inlined IsInitialized check)",
        "desc": "IAP bypass — NOP the cbz that branches to PurchasingUnavailable when _initialized is false",
        "rva": 0x2831CA8,
        "bytes": NOP,
    },
    {
        "name": "Purchaser.IsExistProduct",
        "desc": "IAP bypass — always report product as existing in store",
        "rva": 0x282CE78,
        "bytes": MOV_W0_1 + RET,
    },
    {
        "name": "Purchaser.<BuyProduct>d__24.MoveNext (null _storeController)",
        "desc": "IAP bypass — redirect null _storeController from NRE to cancelled-return path",
        "rva": 0x2834028,
        "bytes": struct.pack("<I", 0xB4001675),
    },
    {
        "name": "PurchaseRealProductAsync.MoveNext (skip CheckPurchasingAlert)",
        "desc": "Fast purchase — skip CheckPurchasingAlert call, awaiter, and CESA dialog (B to post-alert code)",
        "rva": 0x2831CAC,
        "bytes": struct.pack("<I", 0x14000019),
    },
    {
        "name": "Initialize.MoveNext (skip _initialized check)",
        "desc": "Fast purchase — NOP the cbz so Initialize always returns None via builder (skip ~8s GP timeout)",
        "rva": 0x2830834,
        "bytes": NOP,
    },
    {
        "name": "TitleScreen.InitializeMenuButton",
        "desc": "EOS bypass — skip kHideMenuButtonUnixTime check that hides menu button after service end",
        "rva": 0x2F11900,
        "bytes": RET,
    },
]


def patch_libil2cpp(so_path: str) -> int:
    with open(so_path, "r+b") as f:
        file_size = f.seek(0, 2)
        patched = 0
        for p in IL2CPP_PATCHES:
            rva = p["rva"]
            if rva + len(p["bytes"]) > file_size:
                print(f"  [!] SKIP {p['name']}: RVA 0x{rva:X} beyond file size")
                continue

            # Read original bytes for verification (should be a function prologue)
            f.seek(rva)
            orig = f.read(len(p["bytes"]))

            f.seek(rva)
            f.write(p["bytes"])
            patched += 1
            print(f"  {p['name']} @ 0x{rva:X}: {orig.hex()} -> {p['bytes'].hex()}")
            print(f"    {p['desc']}")

    return patched


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(description="Patch decompiled APK for private server")
    p.add_argument("apk_dir", help="Path to apktool-decompiled APK directory")
    p.add_argument(
        "--grpc-addr",
        required=True,
        help="gRPC game server address as host:port (e.g. 10.0.2.2:443)",
    )
    p.add_argument(
        "--http-addr",
        required=True,
        help="HTTP/CDN address as host:port (e.g. 10.0.2.2:8080)",
    )
    p.add_argument(
        "--auth-host",
        help="Auth server host:port for Facebook OAuth redirect (e.g. 10.0.2.2:3000)",
    )
    args = p.parse_args()

    apk = args.apk_dir.rstrip("/")
    auth_host = args.auth_host

    # Parse gRPC address into host + port.
    grpc_host, grpc_port_str = args.grpc_addr.rsplit(":", 1)
    try:
        gp = int(grpc_port_str)
    except ValueError:
        sys.exit(f"[!] Invalid gRPC port in --grpc-addr: {grpc_port_str!r}")
    if not (1 <= gp <= 65535):
        sys.exit(f"[!] gRPC port must be 1..65535, got {gp}")

    web_url = f"http://{args.http_addr}"

    meta = os.path.join(apk, "assets/bin/Data/Managed/Metadata/global-metadata.dat")
    so = os.path.join(apk, "lib/arm64-v8a/libil2cpp.so")
    manifest = os.path.join(apk, "AndroidManifest.xml")
    res_xml = os.path.join(apk, "res/xml")

    for path in (meta, so, manifest):
        if not os.path.isfile(path):
            sys.exit(f"[!] Not found: {path}")

    # The metadata replacement for api.app.nierreincarnation.com uses host only —
    # the client appends _serverPort separately; including a port here would
    # produce "ip:port:port" when the channel is created.
    if gp != 443:
        IL2CPP_PATCHES.append(
            {
                "name": "NetworkConfig.get_ServerPort",
                "desc": f"gRPC port override — return {gp} instead of serialized 443",
                "rva": 0x361D548,
                "bytes": movz_w_imm16(0, gp) + RET,
            }
        )
        IL2CPP_PATCHES.append(
            {
                "name": "InitializeApiClient.OnStateBegin (port override)",
                "desc": f"conductor port override — MOVZ W2, #{gp} before InitializeApiClient call",
                "rva": 0x2DEAF68,
                "bytes": movz_w_imm16(2, gp),
            }
        )
        IL2CPP_PATCHES.append(
            {
                "name": "CalculatorNetworking.InitializeApiClient (port override)",
                "desc": f"catch-all port override — MOVZ W20, #{gp} replaces saved port param",
                "rva": 0x2E1B278,
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

    fb_meta_replacement = None
    if auth_host:
        old_fb = "facebook.com"
        if len(auth_host.encode("utf-8")) > len(old_fb.encode("utf-8")):
            print(
                f"  [!] WARN: auth-host {auth_host!r} longer than {old_fb!r} "
                f"({len(auth_host)} > {len(old_fb)}) — skipping metadata patch "
                f"(smali patch still applies)"
            )
        else:
            fb_meta_replacement = (old_fb, auth_host)
            replacements.append(fb_meta_replacement)

    for old, new in replacements:
        if len(new.encode("utf-8")) > len(old.encode("utf-8")):
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

    print(f"\n[1] Patching global-metadata.dat string literals ...")
    n = patch_metadata_strings(meta, replacements)
    print(f"    {n}/{len(replacements)} strings patched")

    print(
        f"\n[2] Patching libil2cpp.so (SSL bypass + encryption passthrough + IAP bypass + fast purchase) ..."
    )
    n2 = patch_libil2cpp(so)
    print(f"    {n2}/{len(IL2CPP_PATCHES)} methods patched")

    print(f"\n[3] Patching AndroidManifest.xml ...")
    patch_manifest(manifest)

    print(f"\n[4] Creating network_security_config.xml ...")
    create_network_security_config(res_xml)

    if auth_host:
        print("\n[5] Patching Facebook smali (redirect OAuth to auth server) ...")
        n5 = patch_facebook_smali(apk, auth_host)
        print(f"    {n5} smali files patched")

        print("\n[6] Forcing CustomTabUtils.getValidRedirectURI -> default URI ...")
        bypass_cct_redirect_check(apk)

        print("\n[7] Forcing LoginBehavior.NATIVE_WITH_FALLBACK to WebView-only ...")
        force_webview_only_login_behavior(apk)

        print("\n[8] Rewriting CustomTab.openCustomTab -> plain Intent.ACTION_VIEW ...")
        patch_customtab_use_plain_view_intent(apk)

        print(
            "\n[9] Adding shouldOverrideUrlLoading WebResourceRequest overload "
            "to WebDialog$DialogWebViewClient ..."
        )
        patch_webdialog_request_overload(apk)

    print(f"\n[+] Done. Rebuild with:")
    print(f"    apktool b {apk} -o client/patched.apk")
    print(
        f"    apksigner sign --ks client/debug.keystore --ks-pass pass:android {apk.replace('patched/', '')}patched.apk"
    )


if __name__ == "__main__":
    main()
