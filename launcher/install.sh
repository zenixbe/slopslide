#!/usr/bin/env bash
# Put the start/stop icon on the desktop and in the application menu (uninstall: launcher/install.sh --remove).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DESK=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
APPS=${XDG_DATA_HOME:-$HOME/.local/share}/applications
ICONS=${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps
if [ "$1" = "--remove" ]; then
  rm -f "$DESK/sdesk.desktop" "$APPS/sdesk.desktop" "$ICONS/sdesk.svg"
  echo "removed"; exit 0
fi
mkdir -p "$APPS" "$ICONS" "$DESK"
cp "$ROOT/launcher/sdesk.svg" "$ICONS/sdesk.svg"
cat > "$APPS/sdesk.desktop" <<EOT
[Desktop Entry]
Type=Application
Name=Scrolling Desktop
Comment=Start or stop the side-by-side scrolling window layout
Exec=$ROOT/sdesk-toggle toggle
Icon=sdesk
Terminal=false
Categories=Utility;
Actions=start;stop;

[Desktop Action start]
Name=Start
Exec=$ROOT/sdesk-toggle start

[Desktop Action stop]
Name=Stop
Exec=$ROOT/sdesk-toggle stop
EOT
cp "$APPS/sdesk.desktop" "$DESK/sdesk.desktop"
chmod +x "$DESK/sdesk.desktop" "$APPS/sdesk.desktop"
gio set "$DESK/sdesk.desktop" metadata::trusted true 2>/dev/null || true
gtk-update-icon-cache -q -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
update-desktop-database "$APPS" 2>/dev/null || true
echo "installed: $DESK/sdesk.desktop and the application menu"
