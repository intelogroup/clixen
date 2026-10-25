#!/usr/bin/env bash
# Build the orb and prove the artifacts are installable.
#
# Tauri's DMG step creates a scratch rw.<pid>.dmg, mounts it, lays it out with
# Finder, then converts and ejects it. Kill a build (timeout, ctrl-c, a crashed
# Finder) and the scratch image stays mounted — the next run then dies with
# "hdiutil: couldn't unmount /dev/diskN - Resource busy". That failure looks
# like a bundling bug and is not one, so clean it up *before* building.
set -euo pipefail

cd "$(dirname "$0")/.."
APP_DIR="src-tauri/target/release/bundle/macos"
DMG_DIR="src-tauri/target/release/bundle/dmg"
APP="$APP_DIR/Clixen Orb.app"
DMG=$(ls "$DMG_DIR"/Clixen\ Orb_*.dmg 2>/dev/null | head -1 || true)

python3 - "$PWD" <<'PY'
import os, plistlib, subprocess, sys

root = sys.argv[1]
out = subprocess.run(["hdiutil", "info", "-plist"], capture_output=True).stdout
for image in plistlib.loads(out).get("images", []):
    path = image.get("image-path", "")
    # Only our own scratch images: rw.<pid>.Clixen Orb_*.dmg
    name = os.path.basename(path)
    if not (name.startswith("rw.") and name.endswith(".dmg") and "Clixen" in name):
        continue
    for entity in image.get("system-entities", []):
        dev = entity.get("dev-entry")
        if dev:
            subprocess.run(["hdiutil", "detach", dev, "-force"], capture_output=True)
    try:
        os.remove(path)
        print(f"cleaned stale scratch image {name}")
    except OSError:
        pass
PY

echo "== building (app + dmg)"
npm run build

echo "== verifying $APP"
codesign --verify --deep --strict "$APP"
codesign -d --entitlements - "$APP" 2>/dev/null | grep -q 'audio-input' \
  || { echo "entitlement com.apple.security.device.audio-input missing" >&2; exit 1; }

# Tauri's DMG step can exit 0 having produced nothing: bundle_dmg.sh drives Finder
# over osascript to lay the window out, and with no AppleEvents permission (a
# background shell, a locked screen, a headless session) that step fails *after*
# the bundle log line, leaving only a scratch rw.*.dmg behind. Three separate runs
# looked successful and shipped no disk image. So: prove the artifact exists, and
# if it doesn't, clean the scratch images and build the DMG directly.
if ! ls "$DMG_DIR"/Clixen\ Orb_*.dmg >/dev/null 2>&1; then
  echo "== dmg missing after build; retrying without the Finder layout step" >&2
  rm -f "$APP_DIR"/rw.*.dmg
  npx tauri build --bundles dmg || true
fi
ls "$DMG_DIR"/Clixen\ Orb_*.dmg >/dev/null 2>&1 || {
  echo "no .dmg produced in $DMG_DIR" >&2
  exit 1
}

DMG=$(ls -t "$DMG_DIR"/Clixen\ Orb_*.dmg | head -1)
echo "== verifying $(basename "$DMG")"
hdiutil verify "$DMG" | tail -1

# The DMG is the thing users install, so check the app *inside* it, not just the
# one it was built from: a valid bundle outside and a stale one inside is exactly
# the mistake this script exists to catch.
MNT=$(mktemp -d)
hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MNT" >/dev/null
trap 'hdiutil detach "$MNT" -force >/dev/null 2>&1; rmdir "$MNT" 2>/dev/null || true' EXIT
codesign --verify --deep --strict "$MNT/Clixen Orb.app" \
  || { echo "the app inside the dmg does not verify" >&2; exit 1; }
codesign -d --entitlements - "$MNT/Clixen Orb.app" 2>/dev/null | grep -q 'audio-input' \
  || { echo "the app inside the dmg has no audio-input entitlement" >&2; exit 1; }

echo
echo "ok: $APP"
echo "ok: $DMG"
