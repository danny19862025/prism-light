#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Install Prism Monitor for this user: Python environment, app menu entry, and start at sign-in.
# No administrator rights are needed. Turn "Start when I sign in" off later from the tray menu.
set -eu
PRISM_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY="$PRISM_ROOT/.venv/bin/python"
cd "$PRISM_ROOT"

if [ ! -x "$PY" ]; then
    echo 'Creating the Python environment...'
    python3 -m venv .venv
fi
echo 'Installing requirements...'
"$PY" -m pip install --quiet -r requirements.txt

if command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1; then
    "$PY" tools/build_fps_layer.py || echo 'Game FPS layer not built (optional); everything else works.'
fi

# App menu entry (Exec quoted per the XDG spec, as autostart.py does).
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
EXEC=$("$PY" -c 'import sys; from prism_monitor.autostart import _exec_arg as q; print(q(sys.argv[1]), q(sys.argv[2]))' \
    "$PY" "$PRISM_ROOT/monitor_main.py")
cat > "$APPS/prism-monitor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Prism Monitor
Comment=Hardware stats desktop widget
Exec=$EXEC
Path=$PRISM_ROOT
Icon=prism-monitor
Terminal=false
Categories=System;Monitor;
EOF
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$APPS" || true

# The desktop's own widget: KDE Plasma 6 or GNOME Shell. Prism Monitor keeps running in the tray
# and reads the sensors; the widget draws what it publishes (prism_monitor/publish.py).
FONTS="$PRISM_ROOT/prism_monitor/fonts"
NATIVE=''
if command -v kpackagetool6 >/dev/null 2>&1; then
    echo 'Installing the KDE Plasma widget...'
    STAGE=$(mktemp -d)
    cp -r "$PRISM_ROOT/widgets/plasma/org.prismlight.monitor" "$STAGE/"
    mkdir -p "$STAGE/org.prismlight.monitor/contents/fonts"
    cp "$FONTS"/Barlow-*.ttf "$FONTS/OFL-barlow.txt" "$STAGE/org.prismlight.monitor/contents/fonts/"
    # The widget's right-click menu runs Prism Monitor with --do ACTION: give it the exact command.
    "$PY" -c 'import json, shlex, sys
cmd = " ".join(shlex.quote(a) for a in sys.argv[2:])
open(sys.argv[1], "w").write("// Written by Install-Linux.sh\n.pragma library\nvar monitor = %s;\n" % json.dumps(cmd))' \
        "$STAGE/org.prismlight.monitor/contents/ui/command.js" "$PY" "$PRISM_ROOT/monitor_main.py"
    if kpackagetool6 -t Plasma/Applet -l 2>/dev/null | grep -qx 'org.prismlight.monitor'; then
        kpackagetool6 -t Plasma/Applet -u "$STAGE/org.prismlight.monitor" >/dev/null
    else
        kpackagetool6 -t Plasma/Applet -i "$STAGE/org.prismlight.monitor" >/dev/null
    fi
    rm -rf "$STAGE"
    NATIVE='plasma'
fi
if command -v gnome-extensions >/dev/null 2>&1; then
    echo 'Installing the GNOME Shell extension...'
    UUID='prism-monitor@prism-light'
    EXT="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$UUID"
    rm -rf "$EXT" && mkdir -p "$EXT"
    cp "$PRISM_ROOT/widgets/gnome/$UUID"/* "$EXT/"
    USER_FONTS="${XDG_DATA_HOME:-$HOME/.local/share}/fonts/prism-monitor"   # GNOME uses installed fonts
    mkdir -p "$USER_FONTS" && cp "$FONTS"/Barlow-*.ttf "$FONTS/OFL-barlow.txt" "$USER_FONTS/"
    command -v fc-cache >/dev/null 2>&1 && fc-cache -f "$USER_FONTS" >/dev/null 2>&1 || true
    gnome-extensions enable "$UUID" 2>/dev/null || true   # a new extension is found after signing in again
    NATIVE="${NATIVE:+$NATIVE }gnome"
fi

# Turns on "Start when I sign in" (writes ~/.config/autostart/prism-monitor.desktop) and starts
# the monitor; if it is already running, the running copy switches the setting on instead.
# With a desktop widget installed, Prism Monitor's own window is hidden (tray only).
echo 'Enabling start at sign-in and starting Prism Monitor...'
nohup "$PY" "$PRISM_ROOT/monitor_main.py" --launch-at-login ${NATIVE:+--native-widget} >/dev/null 2>&1 &
sleep 2
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/prism-monitor.desktop"
if [ -f "$AUTOSTART" ]; then
    echo "Installed. Prism Monitor will start when you sign in ($AUTOSTART)."
    case "${XDG_CURRENT_DESKTOP:-}:$NATIVE" in
        *KDE*:*plasma*)
            echo 'Add the widget: right-click the desktop > Add or Manage Widgets, search "Prism Monitor"'
            echo 'and drag it onto the desktop. Drag its edge to resize it.';;
        *GNOME*:*gnome*)
            echo 'Sign out and back in once; the widget then appears at the top right of the desktop.'
            echo 'If it does not, run: gnome-extensions enable prism-monitor@prism-light';;
    esac
    [ -n "$NATIVE" ] && echo "Prism Monitor's own window is hidden; Show widget in its tray menu brings it back."
else
    echo 'Installed, but the sign-in entry was not created yet. Use "Start when I sign in" in the tray menu.'
    exit 1
fi
