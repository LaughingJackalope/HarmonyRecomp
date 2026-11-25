#!/bin/bash
# HarmonyRecomp for macOS (Apple Silicon) - builds the game from YOUR game file.
#
#   "Install HarmonyRecomp.command"                       asks for everything
#   "Install HarmonyRecomp.command" --game FILE [--dlc-dir DIR] [--apps DIR]
#
# Part of the installer zip (scripts/package_macos_installer.py): next to it is
# payload/ with the prebuilt ReXGlue SDK, this project's source, CMake and
# Ninja. It builds the game on this Mac with Apple's Command Line Tools and
# puts HarmonyRecomp.app into /Applications (or ~/Applications). Nothing of
# the game is downloaded: the game file comes from your Xbox 360.
#
# Running it again with a newer version replaces the app; settings
# (~/Library/Application Support/HarmonyRecomp) and saves are kept.
# macOS ships bash 3.2: no bash 4 features in here.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
GAME=""
DLC_DIR=""
APPS=""
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
WORK="$HOME/Library/Caches/HarmonyRecomp/work"
LOG_DIR="$HOME/Library/Logs/HarmonyRecomp"
LOG="$LOG_DIR/install.log"

while [ $# -gt 0 ]; do
  case "$1" in
    --game) GAME="$2"; shift 2 ;;
    --dlc-dir) DLC_DIR="$2"; shift 2 ;;
    --apps) APPS="$2"; shift 2 ;;
    --jobs) JOBS="$2"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 1 ;;
  esac
done

say() { echo; echo "==> $*"; }
# AppleScript dialogs; the text goes in as an argument, never as script code.
dialog() {  # dialog TEXT BUTTONS... -> name of the button pressed
  local text="$1"; shift
  local buttons; buttons="$(printf '"%s",' "$@")"; buttons="${buttons%,}"
  osascript - "$text" <<EOF 2>/dev/null || true
on run argv
  button returned of (display dialog (item 1 of argv) with title "HarmonyRecomp" buttons {$buttons} default button 1)
end run
EOF
}
fail() {
  echo "error: $*" >&2
  dialog "$*" "OK" >/dev/null
  exit 1
}

[ "$(uname -s)" = "Darwin" ] || fail "This installer is for macOS."
[ "$(uname -m)" = "arm64" ] || fail "HarmonyRecomp for macOS needs an Apple Silicon Mac (M1 or newer)."
[ -d "$HERE/payload/sdk" ] && [ -d "$HERE/payload/src" ] || \
  fail "payload/ is missing next to the installer. Unpack the whole zip and run it from there."

# --- Apple's Command Line Tools (clang, the macOS SDK, python3) --------------
if ! xcode-select -p >/dev/null 2>&1 || ! /usr/bin/xcrun --find clang++ >/dev/null 2>&1; then
  xcode-select --install >/dev/null 2>&1 || true
  fail "Apple's Command Line Tools are needed to build the game. macOS now offers to install them (a window titled \"Install Command Line Developer Tools\"). Install them, then start this installer again."
fi

# Files from a download are quarantined; macOS would refuse to run the SDK's
# tool and CMake from them. They are only used for this build.
xattr -dr com.apple.quarantine "$HERE/payload" 2>/dev/null || true

# --- What to build from -----------------------------------------------------------
# The game file is an Xbox 360 package: it starts with "LIVE" or "PIRS" and is
# named like a long hex number, from 58410A7A/000D0000/ on the Xbox drive.
is_package() { [ -f "$1" ] && case "$(head -c 4 "$1" 2>/dev/null)" in LIVE|PIRS) true ;; *) false ;; esac; }
GAME_HELP="The game file is the Xbox 360 package from 58410A7A/000D0000/: one file with a long hex name and no extension."
choose_file() {
  osascript -e 'POSIX path of (choose file with prompt "Your Harmony of Despair game file (58410A7A/000D0000/...)")' 2>/dev/null
}
choose_folder() {
  osascript -e 'POSIX path of (choose folder with prompt "Folder with your DLC packages (and the title update)")' 2>/dev/null
}
# Interactive (game picked in a dialog): also ask about DLC and offer to play.
ASK_DLC=0
[ -z "$GAME" ] && ASK_DLC=1
while :; do
  if [ -z "$GAME" ]; then
    GAME="$(choose_file)" || { echo "cancelled"; exit 1; }
  fi
  is_package "$GAME" && break
  if [ ! -f "$GAME" ]; then msg="Game file not found: $GAME"; else msg="$(basename "$GAME") is not an Xbox 360 game package."; fi
  echo "$msg" >&2
  [ "$(dialog "$msg

$GAME_HELP" "Pick another file" "Cancel")" = "Pick another file" ] || exit 1
  GAME=""
done
# Asked only when the game file was picked here too (not for --game).
if [ -z "$DLC_DIR" ] && [ "$ASK_DLC" = 1 ]; then
  if [ "$(dialog "Do you have DLC packages (or the title update) to include?" "No" "Yes, pick the folder")" = "Yes, pick the folder" ]; then
    DLC_DIR="$(choose_folder)" || DLC_DIR=""
  fi
fi
[ -z "$DLC_DIR" ] || [ -d "$DLC_DIR" ] || fail "DLC folder not found: $DLC_DIR"
if [ -z "$APPS" ]; then
  if [ -w /Applications ]; then APPS="/Applications"; else APPS="$HOME/Applications"; fi
fi
mkdir -p "$APPS"

# --- Build -------------------------------------------------------------------------
# In a folder without spaces in its path (the download folder may have some).
say "Preparing $WORK"
rm -rf "$WORK"
mkdir -p "$WORK" "$LOG_DIR"
cp -R "$HERE/payload/." "$WORK/"
TC="$WORK/toolchain"
# Apple's tools first after CMake and Ninja; nothing from Homebrew or elsewhere.
export PATH="$TC/cmake/bin:$TC/ninja:/usr/bin:/bin:/usr/sbin:/sbin"
unset VULKAN_SDK REX_VULKAN_SDK CPATH LIBRARY_PATH CPLUS_INCLUDE_PATH

say "Building (10 to 30 minutes)"
args=(--game "$GAME" --sdk-dir "$WORK/sdk" --jobs "$JOBS")
[ -n "$DLC_DIR" ] && args+=(--dlc-dir "$DLC_DIR")
if ! ( cd "$WORK/src" && /usr/bin/python3 -u scripts/build.py "${args[@]}" ) 2>&1 | tee "$LOG"; then
  fail "The build failed. The log is $LOG"
fi

say "Creating HarmonyRecomp.app"
pargs=(--build-dir "$WORK/src/out/build/mac-arm64-release" --assets "$WORK/src/assets"
       --out "$WORK/dist" --no-zip)
[ -n "$DLC_DIR" ] && pargs+=(--dlc-dir "$DLC_DIR")
if ! ( cd "$WORK/src" && /usr/bin/python3 -u scripts/package_macos.py "${pargs[@]}" ) 2>&1 | tee -a "$LOG"; then
  fail "Creating the app failed. The log is $LOG"
fi

APP="$APPS/HarmonyRecomp.app"
if pgrep -xq harmonyrecomp; then
  fail "HarmonyRecomp is running. Quit it, then start the installer again."
fi
rm -rf "$APP"
ditto "$WORK/dist/HarmonyRecomp.app" "$APP"
rm -rf "$WORK"

say "Done: $APP"
echo "Settings and logs: ~/Library/Application Support/HarmonyRecomp"
if [ "$ASK_DLC" = 1 ] && [ "$(dialog "HarmonyRecomp is installed in $APPS.

It opens fullscreen; Ctrl+Cmd+F switches to a window. F6 opens the settings menu." "Play now" "Close")" = "Play now" ]; then
  open "$APP"
fi
