#!/usr/bin/env python3
"""Create the shareable macOS installer (Apple Silicon): no game inside.

    python3 scripts/build.py --sdk-only            # builds ../rexglue-sdk (once)
    python3 scripts/package_macos_installer.py --cmake cmake-X-macos-universal.tar.gz \\
        --ninja ninja-mac.zip                      # -> dist/HarmonyRecomp-macos-installer.zip

The zip holds
  Install HarmonyRecomp.command   double-click (scripts/install_macos.command)
  READ ME FIRST.txt
  payload/sdk/                    prebuilt ReXGlue SDK (mac-arm64 install folder) with the
                                  Vulkan runtime (loader, MoltenVK + manifest, SPIRV-Tools)
  payload/src/                    this project (tracked files only)
  payload/toolchain/cmake/        CMake (official macOS release: bin/ and share/)
  payload/toolchain/ninja/ninja   Ninja (official macOS release)
  payload/release.txt             version shown in the game

Like the Linux package (scripts/package_linux.py) it contains nothing of the
game: the installer builds the game on the player's Mac from their own game
file and puts HarmonyRecomp.app into /Applications. The player needs Apple's
Command Line Tools (the installer offers to install them).

CMake and Ninja: the official releases, https://github.com/Kitware/CMake/releases
(cmake-<version>-macos-universal.tar.gz) and
https://github.com/ninja-build/ninja/releases (ninja-mac.zip).
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "HarmonyRecomp-macos-installer"
SDK_DEFAULT = ROOT.parent / "rexglue-sdk" / "out" / "install" / "mac-arm64"
VULKAN_LIBS = ("libvulkan.1.dylib", "libvulkan.dylib", "libMoltenVK.dylib",
               "libSPIRV-Tools-shared.dylib")
ICD_CANDIDATES = (Path("/opt/homebrew/etc/vulkan/icd.d/MoltenVK_icd.json"),
                  Path("/usr/local/etc/vulkan/icd.d/MoltenVK_icd.json"))
# Must not end up in the package: game data and anything built from it.
FORBIDDEN_TOP = ("game", "assets", "dlc", "out", "dist")

READ_ME = """HarmonyRecomp for macOS (Apple Silicon) - installer
===================================================

This builds Castlevania: Harmony of Despair for your Mac from YOUR game file
(the Xbox 360 package from 58410A7A/000D0000/, one file with a long hex name).
Nothing of the game is in this download.

1. Open Terminal (Applications > Utilities) and run, with the path of this folder:

       bash ~/Downloads/{name}/"Install HarmonyRecomp.command"

   (Or double-click "Install HarmonyRecomp.command". The first time macOS
   blocks it: System Settings > Privacy & Security > "Open Anyway".)

2. If Apple's Command Line Tools are missing, macOS offers to install them;
   do that (a few minutes), then start the installer again.

3. Pick your game file (and, if you have them, the folder with DLC and the
   title update). The build takes 10 to 30 minutes.

4. HarmonyRecomp is then in your Applications folder. It opens fullscreen;
   Ctrl+Cmd+F switches to a window. F6 settings menu, F2 FPS counter,
   F7 30/60 FPS, F8 achievements (fn+F6 etc. on a Mac keyboard).

Running the installer again (a newer version) replaces the app; settings
(~/Library/Application Support/HarmonyRecomp) and saves
(~/.local/share/harmonyrecomp) are kept.

The build log is ~/Library/Logs/HarmonyRecomp/install.log.
"""


def run(args, **kw):
    print("  $", " ".join(str(a) for a in args))
    return subprocess.run(args, check=True, **kw)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                         check=True).stdout
    names = [n for n in out.decode("utf-8").split("\0") if n]
    bad = [n for n in names if n.split("/")[0] in FORBIDDEN_TOP]
    if bad:
        sys.exit(f"error: tracked game/build files would be packaged: {bad[:5]}")
    return names


def add_sdk(sdk: Path, dst: Path) -> None:
    """The SDK install folder, with its Vulkan runtime as real files plus the
    MoltenVK loader manifest (the SDK install keeps Homebrew's relative links,
    which are broken anywhere else, and has no manifest)."""
    shutil.copytree(sdk, dst, symlinks=True,
                    ignore=shutil.ignore_patterns("vulkan", "pkgconfig"))
    vk_src = sdk / "share" / "rexglue" / "vulkan" / "lib"
    vk = dst / "share" / "rexglue" / "vulkan"
    (vk / "lib").mkdir(parents=True)
    homebrew = (Path("/opt/homebrew/lib"), Path("/opt/homebrew/opt/molten-vk/lib"))
    for name in VULKAN_LIBS:
        src = next((p for p in [vk_src / name] + [h / name for h in homebrew] if p.is_file()),
                   None)  # is_file follows the link; a broken one falls through
        if src is None:
            sys.exit(f"error: {name} not found (SDK vulkan/lib or Homebrew)")
        shutil.copyfile(src, vk / "lib" / name)
        (vk / "lib" / name).chmod(0o755)
    icd_src = next((p for p in ICD_CANDIDATES if p.is_file()), None)
    if icd_src is None:
        sys.exit("error: MoltenVK_icd.json not found; brew install molten-vk")
    icd = json.loads(icd_src.read_text())
    icd["ICD"]["library_path"] = "../../../lib/libMoltenVK.dylib"
    (vk / "share" / "vulkan" / "icd.d").mkdir(parents=True)
    (vk / "share" / "vulkan" / "icd.d" / "MoltenVK_icd.json").write_text(
        json.dumps(icd, indent=4) + "\n")


def add_cmake(archive: Path, dst: Path, scratch: Path) -> None:
    with tarfile.open(archive) as tar:
        tar.extractall(scratch)
    contents = next(scratch.glob("cmake-*/CMake.app/Contents"), None)
    if contents is None:
        sys.exit(f"error: {archive} is not CMake's macOS release (no CMake.app)")
    dst.mkdir(parents=True)
    shutil.copytree(contents / "bin", dst / "bin", symlinks=True,
                    ignore=shutil.ignore_patterns("cmake-gui"))
    shutil.copytree(contents / "share", dst / "share", symlinks=True)
    version = run([str(dst / "bin" / "cmake"), "--version"], capture_output=True,
                  text=True).stdout.splitlines()[0]
    print(f"      {version}")


def add_ninja(archive: Path, dst: Path) -> None:
    dst.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        (dst / "ninja").write_bytes(z.read("ninja"))
    (dst / "ninja").chmod(0o755)
    print("      ninja", run([str(dst / "ninja"), "--version"], capture_output=True,
                             text=True).stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sdk", type=Path, default=SDK_DEFAULT, help="SDK install folder")
    parser.add_argument("--cmake", type=Path, required=True,
                        help="cmake-<version>-macos-universal.tar.gz")
    parser.add_argument("--ninja", type=Path, required=True, help="ninja-mac.zip")
    parser.add_argument("--tag", help="version shown in the game (default: git describe)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        sys.exit("error: run this on macOS")
    if not (args.sdk / "lib" / "librexruntime.dylib").is_file():
        sys.exit(f"error: {args.sdk} is not a mac-arm64 SDK install; "
                 "run python3 scripts/build.py --sdk-only")

    tag = args.tag or subprocess.run(["git", "describe", "--tags", "--always", "--dirty"],
                                     cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = args.out.resolve()
    stage = out / NAME
    if stage.exists():
        shutil.rmtree(stage)
    payload = stage / "payload"
    payload.mkdir(parents=True)

    print("[1/5] SDK and Vulkan runtime")
    add_sdk(args.sdk.resolve(), payload / "sdk")
    print("[2/5] Project source (tracked files)")
    for name in tracked_files():
        src = ROOT / name
        if src.is_file():
            (payload / "src" / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, payload / "src" / name)
    (payload / "release.txt").write_text(tag + "\n")
    print("[3/5] CMake and Ninja")
    scratch = out / f".{NAME}-cmake"
    if scratch.exists():
        shutil.rmtree(scratch)
    add_cmake(args.cmake, payload / "toolchain" / "cmake", scratch)
    shutil.rmtree(scratch)
    add_ninja(args.ninja, payload / "toolchain" / "ninja")
    print("[4/5] Installer script")
    script = stage / "Install HarmonyRecomp.command"
    shutil.copy2(ROOT / "scripts" / "install_macos.command", script)
    script.chmod(0o755)
    (stage / "READ ME FIRST.txt").write_text(READ_ME.format(name=NAME))

    # Last check: no game data. The game file is an STFS package (LIVE/PIRS);
    # extracted data would have default.xex.
    for path in stage.rglob("*"):
        if path.is_file() and not path.is_symlink():
            if path.name.lower().endswith((".xex", ".xexp")):
                sys.exit(f"error: game file in the package: {path}")
            with open(path, "rb") as f:
                if f.read(4) in (b"LIVE", b"PIRS", b"CON "):
                    sys.exit(f"error: Xbox 360 package in the package: {path}")

    zip_path = out / f"{NAME}.zip"
    print(f"[5/5] {zip_path.name}")
    if zip_path.exists():
        zip_path.unlink()
    run(["ditto", "-c", "-k", "--keepParent", str(stage), str(zip_path)])
    shutil.rmtree(stage)
    print(f"\nDone: {zip_path} ({zip_path.stat().st_size >> 20} MB), version {tag}")
    print("It contains nothing of the game and can be shared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
