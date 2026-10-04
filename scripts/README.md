# Lunar Tear Scripts

Tools for patching and inspecting NieR Re[in]carnation client files.

## Quick Start (Google Colab)

No local setup required. Open one of the platform notebooks in [Google Colab](https://colab.research.google.com/), paste your download URLs, and click Run All. Each notebook handles everything: dependencies, client patching, master-data patching, and file downloads.

- Android: **`android/lunar_tear_patcher.ipynb`**
- iOS: **`ios/lunar_tear_patcher.ipynb`** (the patched IPA must be re-signed locally before installing)

## Manual Usage

### Prerequisites

- Python 3
- Android only: `apktool`, `zipalign`, `apksigner`, `keytool`
- `pip install pycryptodome msgpack lz4 protobuf`

### Patch APK (Android)

Patches an apktool-decompiled APK to connect to a private server.

```bash
apktool d game.apk -o patched -f

python3 android/patch_apk.py patched --grpc-addr 10.0.2.2:8003 --http-addr 10.0.2.2:8080 --auth-host 10.0.2.2:3000

apktool b patched -o patched_unaligned.apk
zipalign -p -f 4 patched_unaligned.apk patched.apk

# Generate signing key (one-time):
keytool -genkeypair \
  -keystore debug.keystore \
  -alias androiddebugkey \
  -storepass android \
  -keypass android \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000 \
  -dname "CN=Android Debug,O=Android,C=US"

apksigner sign --ks debug.keystore --ks-pass pass:android patched.apk
```

| Flag | Default | Description |
|------|---------|-------------|
| `apk_dir` | *(required)* | Path to apktool-decompiled APK directory |
| `--grpc-addr` | *(required)* | gRPC game server as `host:port` (e.g. `10.0.2.2:443`) |
| `--http-addr` | *(required)* | HTTP/CDN server as `host:port` (e.g. `10.0.2.2:8080`) |
| `--auth-host` | *(optional)* | Auth server `host:port` (e.g. `10.0.2.2:3000`) |

Notes:

- Both addresses use `host:port` format. The gRPC and HTTP servers can be on different machines.
- Replacement strings must fit existing metadata string lengths (the script validates this).
- The client defaults gRPC to port 443. When the port in `--grpc-addr` is not 443, `libil2cpp.so` is patched to override the port. The HTTP address is baked into the patched URL.
- `zipalign` must run **before** `apksigner` (signing invalidates alignment).
- The script automatically fixes `extractNativeLibs="false"` to `"true"` in the manifest, which prevents crashes after rebuilding with apktool.
- `--auth-host` is optional. When provided, the patcher rewrites the Facebook SDK's smali (DEX) files so the in-game Facebook login button opens your auth server instead of `facebook.com`. The auth server must be running at the given `host:port` (see main README for auth-server setup).

### Patch IPA (iOS)

Patches an unpacked NieR.app inside an IPA to connect to a private server. The script handles unpack/repack itself, so no apktool/zipalign equivalents are needed — only re-signing afterwards.

```bash
python3 ios/patch_ipa.py game.ipa \
  --grpc-addr 10.0.2.2:8003 \
  --http-addr 10.0.2.2:8080 \
  --auth-host 10.0.2.2:3000 \
  -o patched.ipa
```

| Flag | Default | Description |
|------|---------|-------------|
| `ipa` | *(required)* | Path to source IPA |
| `--grpc-addr` | *(required)* | gRPC game server as `host:port` |
| `--http-addr` | *(required)* | HTTP/CDN server as `host:port` |
| `--auth-host` | *(optional)* | Auth server `host:port`. Up to 25 bytes (the framework patch reuses an adjacent `.facebook.com` cstring slot). |
| `-o`, `--output` | `patched.ipa` | Output IPA path |

The patched IPA is **unsigned**. Pick one before installing:

- AltStore / Sideloadly — import `patched.ipa` (auto-resigns with your Apple ID).
- Jailbroken: `ldid -S patched.ipa`.
- macOS with developer cert:
  ```bash
  unzip patched.ipa -d patched_dir
  codesign -f -s 'iPhone Developer: NAME' patched_dir/Payload/NieR.app
  cd patched_dir && zip -r ../patched-signed.ipa Payload
  ```

### Patch Master Data

Extends time-gated content to 2030 by patching EndDatetime fields in the encrypted MasterMemory binary.

```bash
python3 patch_masterdata.py --input original.bin.e --output 20240404193219.bin.e
```

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | `server/assets/release/20240404193219.bin.e` | Input `.bin.e` file |
| `--output` | *(overwrites input)* | Output `.bin.e` file |
| `--dry-run` | | Decrypt + patch + report, no write |
| `--key` | *(built-in)* | AES key as hex string |
| `--iv` | *(built-in)* | AES IV as hex string |
| `--key-file` | | Path to raw key file (16 or 32 bytes) |
| `--iv-file` | | Path to raw IV file (16 bytes) |

Notes:

- `--key`/`--key-file` and `--iv`/`--iv-file` are mutually exclusive pairs.
- Empties `m_maintenance` to prevent maintenance screens. Skips `m_omikuji`.
- Partially patches `m_gimmick_sequence_schedule` (only schedules before 2023-02) to stay under the client's 1024-entry limit.

### Dump Master Data (Optional)

Decrypts and dumps all tables to individual JSON files with named columns. This is for inspection only — the server reads master data directly from the `.bin.e` binary at startup.

```bash
python3 dump_masterdata.py --input original.bin.e --output master_data/
```

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | `server/assets/release/20240404193219.bin.e` | Input `.bin.e` file |
| `--output` | `master_data` | Output directory |
| `--key` | *(built-in)* | AES key as hex string |
| `--iv` | *(built-in)* | AES IV as hex string |
| `--key-file` | | Path to raw key file |
| `--iv-file` | | Path to raw IV file |

Notes:

- `--key`/`--key-file` and `--iv`/`--iv-file` are mutually exclusive pairs.
- Column names are resolved from `schemas.json`. Output files use entity class names when matched (e.g. `EntityMQuestTable.json`), otherwise the raw table name (e.g. `m_quest.json`).

### Patch list.bin

Refreshes `size`, `md5`, and `crc` in Octo `list.bin` entries from the actual files on disk. Run this after swapping any `.assetbundle` (or resource) file under `assets/revisions/<rev>/<platform>/`, or the CDN's md5 check (`server/internal/service/octo.go` in lunar-tear) will reject the file.

```bash
python3 assetbundles/patch_listbin.py path/to/list.bin              # patch one file
python3 assetbundles/patch_listbin.py --all path/to/revision/0      # patch the android + ios list.bins in one pass
python3 assetbundles/patch_listbin.py path/to/list.bin --dry-run    # report changes without writing
python3 assetbundles/patch_listbin.py path/to/list.bin -v           # print per-entry diffs
```

There are exactly two list.bins per revision (`<rev>/android/list.bin` and `<rev>/ios/list.bin`). The catalog only lives under `revisions/0/`; later revision directories hold updated `.assetbundle` files but no list.bin of their own.

| Flag | Default | Description |
|------|---------|-------------|
| `target` | *(required)* | Path to `list.bin` (single mode) or directory (with `--all`) |
| `--all` | | Treat target as a directory, recursively patch every file literally named `list.bin` underneath |
| `--dry-run` | | Compute changes but do not write |
| `-v`, `--verbose` | | Print per-entry size / md5 / crc diffs |

Notes:

- An entry is rewritten only when its `size` or `md5` differs from disk. Unmodified entries are left byte-equivalent so re-runs on a clean tree produce zero writes.
- `crc` is set to `zlib.crc32(file_bytes)` only on entries that change, and is otherwise preserved. The Octo `Data.crc` field is decorative on this client: the public `AssetBundle.LoadFromFileAsync(path)` wrapper passes `crc=0` to Unity (verified at libil2cpp.so RVA `0x5401384`), so Unity's CRC verification is skipped regardless of the value.
- Files missing from disk leave their entry untouched (the CDN already returns 404 gracefully).
- The first run preserves the original as `list.bin.bak`. Writes are atomic (`tmp` → `rename`).
