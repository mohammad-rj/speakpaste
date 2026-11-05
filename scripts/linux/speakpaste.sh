#!/bin/sh
# Start SpeakPaste from this checkout with the AppIndicator tray backend.
cd "$(dirname "$0")/../.." || exit 1
export PYSTRAY_BACKEND=appindicator
exec .venv/bin/python speakpaste.py "$@"
