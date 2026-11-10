#!/usr/bin/env bash
# Update yt-dlp to the latest nightly in the installed app and/or the dev venv.
#
# Run this whenever downloads start failing site-wide (especially YouTube) —
# sites change their internals and yt-dlp's fixes land in the *nightly* channel
# first, often weeks before a stable release. No rebuild needed; just restart
# YTDownload afterward.
#
#   ./update.sh
#
set -uo pipefail
cd "$(dirname "$0")"

updated=0

update_venv() {
  local py="$1" label="$2"
  [ -x "$py" ] || return 0
  echo "Updating yt-dlp in $label…"
  # Use 'python -m pip', NOT the pip wrapper: a .app venv built in the project
  # dir and then moved to /Applications has a stale absolute shebang in bin/pip.
  if "$py" -m pip install -q -U --pre "yt-dlp[default]"; then
    echo "  now: $("$py" -c 'import yt_dlp; print(yt_dlp.version.__version__)')"
    updated=1
  else
    echo "  FAILED to update $label"
  fi
}

update_venv "/Applications/YTDownload.app/Contents/Resources/venv/bin/python" "the installed app (/Applications/YTDownload.app)"
update_venv "./.venv/bin/python" "the dev venv (.venv)"

if [ "$updated" = "0" ]; then
  echo "No YTDownload venv found. Run ./start.sh or ./build_app.sh first."
  exit 1
fi

echo "Done. Quit and reopen YTDownload for the update to take effect."
