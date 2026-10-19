#!/bin/zsh
# Build "Computer Use.app" and put "Computer Use <version>.dmg" next to the project folder.
# The build runs outside ~/Documents: iCloud/Finder attach extended attributes there, which codesign rejects.
set -e
cd "${0:A:h}/.."
OUT=$(mktemp -d /tmp/cu-build.XXXX)
VERSION=$(uv run python -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')
DMG="../Computer Use $VERSION.dmg"
uv run pyinstaller --noconfirm --windowed --name "Computer Use" \
  --distpath "$OUT/dist" --workpath "$OUT/work" --specpath "$OUT" \
  --icon "$PWD/packaging/AppIcon.icns" \
  --osx-bundle-identifier com.computeruse.lite \
  --collect-submodules google.genai --collect-data google.genai --exclude-module google.genai.tests \
  --collect-submodules mcp.server --collect-submodules mcp.shared --exclude-module mcp.cli \
  --collect-submodules rumps --paths "$PWD" --collect-submodules cu \
  --hidden-import AppKit --hidden-import Quartz --hidden-import ApplicationServices \
  --exclude-module tkinter \
  packaging/launcher.py

APP="$OUT/dist/Computer Use.app"
PL="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PL"
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string 'Computer Use controls other apps to carry out your tasks.'" "$PL"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PL"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"   # ad-hoc signature so macOS can remember permissions
codesign --verify --deep "$APP"

# --- DMG: logo on the file and the disk, background with an arrow, icons laid out ---
VOL="Computer Use"
RW="$OUT/rw.dmg"
hdiutil create -size 200m -fs HFS+ -volname "$VOL" -ov "$RW" >/dev/null
MNT=$(hdiutil attach -readwrite -noverify -noautoopen "$RW" | awk -F'\t' '/Apple_HFS/ {print $NF}')
cp -R "$APP" "$MNT/"
ln -s /Applications "$MNT/Applications"
mkdir "$MNT/.background"
tiffutil -cathidpicheck packaging/dmg-background.png packaging/dmg-background@2x.png -out "$MNT/.background/background.tiff" >/dev/null
cp packaging/AppIcon.icns "$MNT/.VolumeIcon.icns"
SetFile -a C "$MNT"   # use .VolumeIcon.icns as the disk's icon
osascript <<OSA || echo "warning: Finder layout skipped (allow Automation for Finder to get the styled window)"
tell application "Finder"
  tell disk "$VOL"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set bounds of container window to {200, 120, 860, 520}
    set opts to icon view options of container window
    set arrangement of opts to not arranged
    set icon size of opts to 112
    set text size of opts to 13
    set background picture of opts to file ".background:background.tiff"
    set position of item "Computer Use.app" of container window to {170, 215}
    set position of item "Applications" of container window to {490, 215}
    update without registering applications
    delay 1
    close
  end tell
end tell
OSA
chmod -Rf go-w "$MNT" 2>/dev/null || true
sync
hdiutil detach "$MNT" -quiet || hdiutil detach "$MNT" -force -quiet
rm -f ../"Computer Use"*.dmg(N)   # keep only the newest installer at the top
hdiutil convert "$RW" -format UDZO -imagekey zlib-level=9 -ov -o "$DMG" >/dev/null

# Logo on the .dmg file itself in Finder (stored as a Finder attribute, so it shows on this Mac).
uv run python - "$DMG" <<'PY'
import sys
from AppKit import NSImage, NSWorkspace
NSWorkspace.sharedWorkspace().setIcon_forFile_options_(NSImage.alloc().initWithContentsOfFile_("packaging/AppIcon.icns"), sys.argv[1], 0)
PY

du -sh "$APP" "$DMG"
echo "app: $APP"
