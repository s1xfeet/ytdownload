#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="YTDownload"
APP_DIR="$APP_NAME.app"
CONTENTS="$APP_DIR/Contents"
RES="$CONTENTS/Resources"
MACOS="$CONTENTS/MacOS"

echo "Building $APP_DIR…"

rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RES"

# Copy app source.
cp -R app "$RES/app"
cp desktop.py "$RES/desktop.py"
cp requirements.txt "$RES/requirements.txt"

# Build a self-contained venv inside the bundle.
echo "Creating venv inside bundle…"
python3 -m venv "$RES/venv"
"$RES/venv/bin/pip" install -q --upgrade pip
"$RES/venv/bin/pip" install -q -r "$RES/requirements.txt"
# yt-dlp from the nightly channel: the stable release lags behind YouTube's
# frequent changes (e.g. PO-token 403s), and fixes land in nightly first.
"$RES/venv/bin/pip" install -q -U --pre "yt-dlp[default]"

# Info.plist
cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>YTDownload</string>
  <key>CFBundleDisplayName</key><string>YTDownload</string>
  <key>CFBundleIdentifier</key><string>local.ytdownload</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleExecutable</key><string>YTDownload</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSUIElement</key><false/>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key><true/>
  </dict>
</dict>
</plist>
PLIST

# Launcher script — opens the native window (desktop.py picks a free port,
# starts the server, and shows a WebKit window; no browser involved).
cat > "$MACOS/YTDownload" <<'LAUNCH'
#!/usr/bin/env bash

# Ensure ffmpeg/yt-dlp from Homebrew are on PATH when launched from Finder.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
cd "$DIR"

# A bundled GUI app has no console — log to a file so failures are diagnosable.
LOG="${TMPDIR:-/tmp}/ytdownload.log"

# The bundled wheels are built for the Mac's native arch, but a universal2
# Python can be launched in the x86_64 slice by LaunchServices, which then
# can't load the arm64 wheels. Force arm64 on Apple Silicon; fall back to a
# plain launch on Intel (where the wheels are x86_64 anyway).
if arch -arm64 true 2>/dev/null; then
  exec arch -arm64 ./venv/bin/python desktop.py >"$LOG" 2>&1
else
  exec ./venv/bin/python desktop.py >"$LOG" 2>&1
fi
LAUNCH
chmod +x "$MACOS/YTDownload"

echo "Built $APP_DIR"
echo "Run it: open $APP_DIR"
echo "Install: mv $APP_DIR /Applications/"
