#!/usr/bin/env python3
"""Cross-build the grant helper so a panel needs no Go toolchain.

The panel writes saves by running `tools/grant/grant` (Windows: `grant.exe`),
which `setup.*` compiles from `tools/grant/src` against the game-server module.
This script prebuilds that binary for every desktop platform and stores it as

    tools/grant/prebuilt/<os>-<arch>/grant[.exe]

`web/config.py` prefers a prebuilt binary for the running platform, so an
installed panel (or a release download) works without Go on PATH.

    python tools/grant/build_prebuilt.py                 # all six targets
    python tools/grant/build_prebuilt.py win32-amd64     # one target
    python tools/grant/build_prebuilt.py --clean         # remove prebuilt/

The game-server checkout is found with LUNAR_SERVER_DIR / LUNAR_TEAR_DIR, the
same way setup.* finds it. Cross-compiling needs only the Go toolchain: the
helper is pure Go plus the server's own packages.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "tools" / "grant" / "src"
PREBUILT = ROOT / "tools" / "grant" / "prebuilt"

TARGETS = {
    "win32-amd64": ("windows", "amd64"),
    "win32-arm64": ("windows", "arm64"),
    "linux-amd64": ("linux", "amd64"),
    "linux-arm64": ("linux", "arm64"),
    "darwin-amd64": ("darwin", "amd64"),
    "darwin-arm64": ("darwin", "arm64"),
}


def go_binary() -> str:
    for name in ("go", "go.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("go not found on PATH (install Go, or copy a prebuilt binary)")


def server_dir() -> Path:
    for key in ("LUNAR_SERVER_DIR", "LUNAR_TEAR_DIR"):
        if os.environ.get(key):
            return Path(os.environ[key]).resolve()
    for candidate in (ROOT.parent, ROOT.parent / "lunar-server", ROOT.parent / "lunar-tear"):
        if (candidate / "server" / "go.mod").is_file():
            return candidate.resolve()
    raise SystemExit("game-server checkout not found; set LUNAR_SERVER_DIR")


def main(argv: list[str]) -> int:
    if "--clean" in argv:
        shutil.rmtree(PREBUILT, ignore_errors=True)
        print(f"removed {PREBUILT}")
        return 0
    wanted = [a for a in argv if not a.startswith("-")] or list(TARGETS)
    unknown = [w for w in wanted if w not in TARGETS]
    if unknown:
        raise SystemExit(f"unknown target(s): {', '.join(unknown)}\nknown: {', '.join(TARGETS)}")

    server = server_dir()
    cmd_dir = server / "cmd" / "lunar-base-grant"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    for source in SRC.glob("*.go"):
        shutil.copyfile(source, cmd_dir / source.name)

    for target in wanted:
        goos, goarch = TARGETS[target]
        name = "grant.exe" if goos == "windows" else "grant"
        out = PREBUILT / target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": "0"}
        print(f"building {target} -> {out.relative_to(ROOT)}", flush=True)
        # Write through a relative path: a Windows go.exe started from WSL cannot
        # open a /mnt/... output path, and the relative path resolves on both.
        staged = Path(".grant-build") / target / name
        staged.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [go_binary(), "build", "-trimpath", "-ldflags", "-s -w", "-o", staged.as_posix(), "./cmd/lunar-base-grant/"],
            cwd=server, env=env, check=True,
        )
        shutil.move(str(server / staged), str(out))
        # Cross-built files lose the permission bit when copied from Windows.
        out.chmod(0o755)
        print(f"  {out.stat().st_size / 1_048_576:.1f} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
