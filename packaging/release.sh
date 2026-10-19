#!/bin/zsh
# Publish a release the app's updater picks up: bump __version__ in cu/__init__.py, commit, then
#   ./packaging/release.sh "What changed"
set -e
cd "${0:A:h}/.."
VERSION=$(uv run python -c 'import cu;print(cu.__version__)')
git diff --quiet && git diff --cached --quiet || { echo "Commit your changes first."; exit 1; }
if gh release view "v$VERSION" >/dev/null 2>&1; then echo "v$VERSION already exists; bump cu/__init__.py."; exit 1; fi
./packaging/build.sh
git -c credential.helper= -c credential.helper='!gh auth git-credential' push -q
gh release create "v$VERSION" --target "$(git rev-parse HEAD)" --title "Computer Use $VERSION" \
  --notes "${1:-Bug fixes and improvements.}" \
  "../Computer Use $VERSION.dmg" "../Computer Use Voice Pack $VERSION.zip"
echo "Released v$VERSION. Installed apps update within 6 hours, or at once from Settings › Check for Updates."
