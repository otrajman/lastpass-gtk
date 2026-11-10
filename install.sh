#!/bin/sh
# Install LastPass GTK for the current user (~/.local) from a source checkout.
# No root needed. For a system-wide install use the .deb; for any distro, the AppImage.
#
#   ./install.sh            install
#   ./install.sh uninstall  remove
#
# Uses the lpass built by scripts/build-lpass.sh (build/lpass/lpass), falling back
# to an `lpass` already on PATH.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
prefix="${PREFIX:-$HOME/.local}"
appid=io.github.otrajman.LastPassGtk
share="$prefix/share/lastpass-gtk"
desktop="$prefix/share/applications/$appid.desktop"
icon="$prefix/share/icons/hicolor/scalable/apps/$appid.svg"
legacy="$prefix/share/applications/io.github.lastpass_gtk.desktop"  # pre-0.1.0 name
launcher="$prefix/bin/lastpass-gtk"

if [ "${1:-}" = "uninstall" ]; then
    rm -f "$launcher" "$desktop" "$icon" "$legacy"
    rm -rf "$share"
    echo "Removed LastPass GTK from $prefix"
    exit 0
fi

lpass_bin=""
for c in "$here/build/lpass/lpass" "$here/../lastpass-cli/build/lpass" "$(command -v lpass || true)"; do
    if [ -n "$c" ] && [ -x "$c" ]; then lpass_bin="$c"; break; fi
done
if [ -z "$lpass_bin" ]; then
    echo "lpass not found. Run scripts/build-lpass.sh first (see README.md)." >&2
    exit 1
fi

python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")' \
    || { echo "Need python3-gi with GTK 4 and libadwaita 1.5+ (see README.md)." >&2; exit 1; }

mkdir -p "$share/libexec" "$prefix/bin" "$(dirname "$desktop")" "$(dirname "$icon")"
rm -f "$legacy"
rm -rf "$share/lastpass_gtk"
cp -r "$here/lastpass_gtk" "$share/"
find "$share/lastpass_gtk" -name __pycache__ -prune -exec rm -rf {} +
chmod +x "$share/lastpass_gtk/askpass.py"
install -m 755 "$lpass_bin" "$share/libexec/lpass"

cat > "$launcher" <<EOF
#!/bin/sh
export PYTHONPATH="$share\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -P -m lastpass_gtk "\$@"
EOF
chmod +x "$launcher"

sed "s|^Exec=.*|Exec=$launcher|" "$here/data/$appid.desktop" > "$desktop"
install -m 644 "$here/data/icons/$appid.svg" "$icon"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$(dirname "$desktop")" || true

echo "Installed. Launch \"LastPass GTK\" from your app menu, or run: $launcher"
