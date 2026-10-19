#!/bin/zsh
# Build "Computer Use.app" and "Computer Use.dmg" into dist/.
# The build runs outside ~/Documents: iCloud/Finder attach extended attributes there, which codesign rejects.
set -e
cd "${0:A:h}/.."
OUT=$(mktemp -d /tmp/cu-build.XXXX)
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
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $(uv run python -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')" "$PL"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"   # ad-hoc signature so macOS can remember permissions
codesign --verify --deep "$APP"

STAGE="$OUT/dmg" && mkdir -p "$STAGE" && cp -R "$APP" "$STAGE/" && ln -s /Applications "$STAGE/Applications"
mkdir -p dist
hdiutil create -volname "Computer Use" -srcfolder "$STAGE" -ov -format UDZO "dist/Computer Use.dmg" >/dev/null
du -sh "$APP" "dist/Computer Use.dmg"
echo "app: $APP"
