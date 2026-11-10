#!/bin/sh
# Build dist/LastPass_GTK-<version>-<arch>.AppImage: a self-contained bundle of the app,
# the patched lpass, Python, PyGObject, GTK 4 and libadwaita.
#
# Run on Ubuntu 24.04 (see packaging/Dockerfile or `make appimage`). The result runs on
# distros with glibc >= 2.39 (Ubuntu 24.04+, Debian 13+, Fedora 40+, Arch, openSUSE TW, ...).
set -eu
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
version="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' lastpass_gtk/app.py)"
arch="$(uname -m)"
triplet="$(gcc -dumpmachine 2>/dev/null || echo "$arch-linux-gnu")"
appid=io.github.otrajman.LastPassGtk
work="$root/build/appimage"
app="$work/AppDir"
out="$root/dist"
sys_lib="/usr/lib/$triplet"
mkdir -p "$out" "$work"

[ -x build/lpass-static/lpass ] || STATIC_LIBXML2=1 scripts/build-lpass.sh build/lpass-static

rm -rf "$app"
mkdir -p "$app/usr/bin" "$app/usr/lib/lastpass-gtk/libexec" "$app/usr/lib/python3/dist-packages" \
         "$app/usr/lib/girepository-1.0" "$app/usr/lib/gio/modules" "$app/usr/share/glib-2.0/schemas" \
         "$app/usr/share/icons" "$app/usr/share/applications" "$app/usr/share/metainfo" \
         "$app/usr/share/icons/hicolor/scalable/apps"

echo "== app + lpass"
cp -r lastpass_gtk "$app/usr/lib/lastpass-gtk/"
chmod 755 "$app/usr/lib/lastpass-gtk/lastpass_gtk/askpass.py"
install -m 755 build/lpass-static/lpass "$app/usr/lib/lastpass-gtk/libexec/lpass"

echo "== python"
pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
install -m 755 "$(readlink -f /usr/bin/python3)" "$app/usr/bin/python3"
cp -a "/usr/lib/python$pyver" "$app/usr/lib/"
( cd "$app/usr/lib/python$pyver" && rm -rf test idlelib tkinter turtledemo ensurepip lib2to3 \
      pydoc_data config-* site-packages dist-packages )
find "$app/usr/lib/python$pyver" -name __pycache__ -prune -exec rm -rf {} +
cp -a /usr/lib/python3/dist-packages/gi "$app/usr/lib/python3/dist-packages/"
find "$app/usr/lib/python3" -name __pycache__ -prune -exec rm -rf {} +

echo "== GObject introspection, GIO, GSettings"
cp "$sys_lib"/girepository-1.0/*.typelib "$app/usr/lib/girepository-1.0/"
# Typelibs dlopen() their libraries by name (e.g. libharfbuzz-gobject), which plain ELF
# dependency scanning can't see, so bundle them explicitly.
for lib in $(for t in "$app"/usr/lib/girepository-1.0/*.typelib; do
                 strings "$t" | grep -m1 -E '^lib[^ ]*\.so' | tr ',' ' '; done | sort -u); do
    [ -e "$sys_lib/$lib" ] && cp -L "$sys_lib/$lib" "$app/usr/lib/$lib"
done
cp "$sys_lib"/gio/modules/libdconfsettings.so "$app/usr/lib/gio/modules/"
cp /usr/share/glib-2.0/schemas/org.gtk.gtk4.*.xml "$app/usr/share/glib-2.0/schemas/"
glib-compile-schemas "$app/usr/share/glib-2.0/schemas"

echo "== gdk-pixbuf loaders"
pixdir="$sys_lib/gdk-pixbuf-2.0/2.10.0"
mkdir -p "$app/usr/lib/gdk-pixbuf-2.0/loaders"
cp "$pixdir"/loaders/*.so "$app/usr/lib/gdk-pixbuf-2.0/loaders/"

echo "== icons"
cp -a /usr/share/icons/Adwaita "$app/usr/share/icons/"
mkdir -p "$app/usr/share/icons/hicolor" && cp /usr/share/icons/hicolor/index.theme "$app/usr/share/icons/hicolor/"
install -m 644 "data/icons/$appid.svg" "$app/usr/share/icons/hicolor/scalable/apps/"
install -m 644 "data/$appid.desktop" "$app/usr/share/applications/"
install -m 644 "data/$appid.metainfo.xml" "$app/usr/share/metainfo/$appid.appdata.xml"

echo "== shared libraries"
python3 -I packaging/appimage/deploy_libs.py "$app"

# loaders.cache with a placeholder that AppRun replaces with the mount point
"$sys_lib/gdk-pixbuf-2.0/gdk-pixbuf-query-loaders" "$app"/usr/lib/gdk-pixbuf-2.0/loaders/*.so \
    | sed "s|$app|@APPDIR@|g" > "$app/usr/lib/gdk-pixbuf-2.0/loaders.cache.in"

echo "== AppDir root"
install -m 755 packaging/appimage/AppRun "$app/AppRun"
cp "data/$appid.desktop" "$app/"
cp "data/icons/$appid.svg" "$app/"
ln -sf "$appid.svg" "$app/.DirIcon"

echo "== appimagetool"
tool="$work/appimagetool-$arch.AppImage"
if [ ! -x "$tool" ]; then
    wget -q -O "$tool" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$arch.AppImage"
    chmod +x "$tool"
fi
target="$out/LastPass_GTK-$version-$arch.AppImage"
ARCH="$arch" APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream "$app" "$target" >"$work/appimagetool.log" 2>&1 \
    || { cat "$work/appimagetool.log"; exit 1; }
echo "built $target ($(du -h "$target" | cut -f1))"
