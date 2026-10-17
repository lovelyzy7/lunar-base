# Grant helper binaries

The panel writes saves by running a small Go program, `tools/grant/grant`
(`grant.exe` on Windows). Its sources are in `src/`; they must live inside the
game-server module, so `setup.sh` / `setup.bat` copy them into
`<game-server>/cmd/lunar-base-grant/` and run `go build`.

That needs a Go toolchain. Two ways to avoid it:

## 1. Prebuilt binary for this platform

```sh
python tools/grant/build_prebuilt.py              # linux-arm64 (this project's target)
python tools/grant/build_prebuilt.py --all        # every desktop platform
python tools/grant/build_prebuilt.py win32-amd64  # one named target
```

The result lands in `prebuilt/<os>-<arch>/`:

| target         | file                     |
| -------------- | ------------------------ |
| `win32-amd64`  | `grant.exe`              |
| `win32-arm64`  | `grant.exe`              |
| `linux-amd64`  | `grant`                  |
| `linux-arm64`  | `grant`                  |
| `darwin-amd64` | `grant`                  |
| `darwin-arm64` | `grant`                  |

`web/config.py` looks there first (exact match on `sys.platform` and
`platform.machine()`), then falls back to the binary `setup.*` builds. Binaries
are not committed: they are release assets (~17 MB each, `-s -w`).

`setup.sh` / `setup.bat` skip the Go build when a matching prebuilt exists, so a
machine without Go still gets a working panel.

## 2. Point at any build

```sh
LUNAR_BASE_GRANT=/opt/bin/grant ./start.sh      # or set it in the environment
```

Windows: `set LUNAR_BASE_GRANT=C:\tools\grant.exe` before `start.bat`.

## Android

The Bunker app does not use this binary at all: `android_runtime.adapt_services()`
routes the same Go engine through `lunar_server`'s native bridge, so the panel on
the phone never spawns a process. `test_services.py` checks that wiring.
