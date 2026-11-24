#!/usr/bin/env python3
"""Wrap a finished macOS build into HarmonyRecomp.app (and a .zip of it).

    python3 scripts/build.py              # build first
    python3 scripts/package_macos.py      # -> dist/HarmonyRecomp.app, dist/HarmonyRecomp-macos-arm64.zip

The app CONTAINS THE GAME: the recompiled code and the extracted game data
from your own copy. It is for your own Macs only. Keep it in private storage
and never share it (see the README: never share game/, assets/, generated/ or
builds made from them).

Layout:
  Contents/MacOS/harmonyrecomp           executable
  Contents/MacOS/*.dylib                 SDK runtime, GPU plugin, character modules
  Contents/Resources/assets/             game data (from assets/, without DLC)
  Contents/Resources/vulkan/lib/         Vulkan loader, MoltenVK, SPIRV-Tools
  Contents/Resources/vulkan/share/vulkan/icd.d/MoltenVK_icd.json
  Contents/Resources/harmonyrecomp.toml  default settings, copied on first start

At runtime the app keeps settings and logs in
~/Library/Application Support/HarmonyRecomp (src/harmonyrecomp_app.h), and
the SDK finds the bundled Vulkan runtime under Contents/Resources/vulkan.
Nothing from Homebrew or this repository is needed on the target Mac.
"""

import argparse
import json
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "HarmonyRecomp"
BUNDLE_ID = "io.github.harmonyrecomp.macos"
# Where Homebrew's molten-vk keeps its loader manifest (it points at ../../../lib).
ICD_CANDIDATES = [
    Path("/opt/homebrew/etc/vulkan/icd.d/MoltenVK_icd.json"),
    Path("/usr/local/etc/vulkan/icd.d/MoltenVK_icd.json"),
]
MOLTENVK_CANDIDATES = [
    Path("/opt/homebrew/opt/molten-vk/lib/libMoltenVK.dylib"),
    Path("/usr/local/opt/molten-vk/lib/libMoltenVK.dylib"),
]
# Settings that differ from the repository template for the app.
APP_SETTINGS = """
# ---- macOS app defaults (scripts/package_macos.py) --------------------------
# Releases on GitHub are Windows/Linux builds; there is nothing to update to.
check_for_updates = false
"""

INSTALL_NOTES = """HarmonyRecomp for macOS (Apple Silicon)
=======================================

This app contains the game, built from your own copy. Keep it private.

Install
-------
1. Move HarmonyRecomp.app to your Applications folder.
2. The app is not signed by Apple, so macOS blocks it once after a download.
   Either open Terminal and run:

       xattr -dr com.apple.quarantine /Applications/HarmonyRecomp.app

   or double-click the app, then System Settings > Privacy & Security >
   "Open Anyway" (at the bottom), and confirm.
3. Start it. It opens fullscreen; Ctrl+Cmd+F switches to a window.

Keys: F6 settings menu (controller works there), F2 FPS counter,
Ctrl+Cmd+F fullscreen. On a Mac keyboard F-keys may need fn (fn+F6).

Where things are
----------------
Settings and logs:  ~/Library/Application Support/HarmonyRecomp
Saves:              ~/.local/share/harmonyrecomp/B13EBABEBABEBABE/58410A7A/00000001/dchd_savedata

To bring your saves from another Mac, copy that dchd_savedata folder to the
same place on this Mac while the game is closed (Finder: Cmd+Shift+G and
paste the path; ~/.local is hidden otherwise).
"""


def run(args):
    print("  $", " ".join(str(a) for a in args))
    subprocess.run(args, check=True)


def first_existing(paths, what):
    for p in paths:
        if p.exists():
            return p
    sys.exit(f"error: {what} not found (tried {', '.join(str(p) for p in paths)}); "
             "brew install molten-vk vulkan-loader")


def copy_tree_files(src: Path, dst: Path):
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_symlink():
            target.symlink_to(item.readlink())
        elif item.is_file():
            shutil.copy2(item, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=Path,
                        default=ROOT / "out" / "build" / "mac-arm64-release")
    parser.add_argument("--assets", type=Path, default=ROOT / "assets")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--no-zip", action="store_true", help="only create the .app")
    args = parser.parse_args()

    if platform.system() != "Darwin":
        sys.exit("error: run this on macOS")
    build, assets, out = args.build_dir.resolve(), args.assets.resolve(), args.out.resolve()
    exe = build / "harmonyrecomp"
    if not exe.is_file():
        sys.exit(f"error: {exe} not found; run python3 scripts/build.py first")
    if not (assets / "default.xex").is_file():
        sys.exit(f"error: {assets} has no default.xex")
    if not (build / "librexgpu-xenos.dylib").is_file():
        sys.exit("error: the GPU plugin librexgpu-xenos.dylib is missing next to the executable")

    app = out / f"{APP_NAME}.app"
    contents = app / "Contents"
    macos, resources = contents / "MacOS", contents / "Resources"
    if app.exists():
        shutil.rmtree(app)
    macos.mkdir(parents=True)
    resources.mkdir()

    print(f"[1/5] Executable and libraries -> {macos.relative_to(out)}")
    shutil.copy2(exe, macos / "harmonyrecomp")
    for lib in sorted(build.glob("*.dylib")):
        shutil.copy2(lib, macos / lib.name)

    print("[2/5] Vulkan runtime (loader, MoltenVK, manifest)")
    vulkan = resources / "vulkan"
    staged = build / "vulkan" / "lib"
    if staged.is_dir():
        copy_tree_files(staged, vulkan / "lib")
    else:
        (vulkan / "lib").mkdir(parents=True)
    if not (vulkan / "lib" / "libMoltenVK.dylib").exists():
        shutil.copy2(first_existing(MOLTENVK_CANDIDATES, "libMoltenVK.dylib"),
                     vulkan / "lib" / "libMoltenVK.dylib")
    for needed in ("libvulkan.1.dylib", "libMoltenVK.dylib"):
        if not (vulkan / "lib" / needed).exists():
            sys.exit(f"error: {needed} missing from {staged}")
    icd_src = first_existing(ICD_CANDIDATES, "MoltenVK_icd.json")
    icd = json.loads(icd_src.read_text())
    icd["ICD"]["library_path"] = "../../../lib/libMoltenVK.dylib"
    icd_dir = vulkan / "share" / "vulkan" / "icd.d"
    icd_dir.mkdir(parents=True)
    (icd_dir / "MoltenVK_icd.json").write_text(json.dumps(icd, indent=4) + "\n")

    print("[3/5] Game data and default settings")
    shutil.copytree(assets, resources / "assets", ignore=shutil.ignore_patterns("dlc", ".*"))
    settings = (ROOT / "harmonyrecomp.toml").read_text() + APP_SETTINGS
    (resources / "harmonyrecomp.toml").write_text(settings)

    version = "dev"
    try:
        version = subprocess.run(["git", "-C", str(ROOT), "describe", "--tags", "--always"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump({
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": "harmonyrecomp",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.0.0",
            "CFBundleVersion": version,
            "LSMinimumSystemVersion": "14.0",
            "LSArchitecturePriority": ["arm64"],
            "LSApplicationCategoryType": "public.app-category.action-games",
            "NSHighResolutionCapable": True,
            "NSSupportsAutomaticGraphicsSwitching": True,
            "GCSupportsControllerUserInteraction": True,
        }, f)

    print("[4/5] Ad-hoc code signature (required for arm64 code)")
    run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    run(["codesign", "--verify", "--deep", "--strict", str(app)])

    if not args.no_zip:
        name = f"{APP_NAME}-macos-{platform.machine()}"
        zip_path = out / f"{name}.zip"
        print(f"[5/5] {zip_path.name}")
        staging = out / name
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        run(["ditto", str(app), str(staging / app.name)])
        (staging / "READ ME FIRST.txt").write_text(INSTALL_NOTES)
        if zip_path.exists():
            zip_path.unlink()
        run(["ditto", "-c", "-k", "--keepParent", str(staging), str(zip_path)])
        shutil.rmtree(staging)
    print(f"\nDone: {app}")
    print("This app contains the game from your own copy: keep it private, do not share it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
