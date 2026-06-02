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
cp requirements.txt "$RES/requirements.txt"

# Build a self-contained venv inside the bundle.
echo "Creating venv inside bundle…"
python3 -m venv "$RES/venv"
"$RES/venv/bin/pip" install -q --upgrade pip
"$RES/venv/bin/pip" install -q -r "$RES/requirements.txt"

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
</dict>
</plist>
PLIST

# Launcher script.
cat > "$MACOS/YTDownload" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail

# Ensure ffmpeg/yt-dlp from Homebrew are on PATH when launched from Finder.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
PORT="${YTDOWNLOAD_PORT:-8765}"

while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

URL="http://127.0.0.1:$PORT"
( sleep 1.2; open "$URL" ) &

cd "$DIR"
exec ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
LAUNCH
chmod +x "$MACOS/YTDownload"

echo "Built $APP_DIR"
echo "Run it: open $APP_DIR"
echo "Install: mv $APP_DIR /Applications/"
