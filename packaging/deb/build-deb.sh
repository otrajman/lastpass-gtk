#!/bin/sh
# Build dist/lastpass-gtk_<version>_<arch>.deb. Run on Ubuntu 24.04 (see packaging/Dockerfile
# or `make deb`), so the bundled lpass links against the oldest supported libraries.
set -eu
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
version="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' lastpass_gtk/app.py)"
arch="$(dpkg --print-architecture)"
appid=io.github.otrajman.LastPassGtk
stage="$root/build/deb/stage"
out="$root/dist"

STATIC_LIBXML2=1 scripts/build-lpass.sh build/lpass-static  # always rebuild: never ship a stale lpass

rm -rf "$stage"
mkdir -p "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/lib/lastpass-gtk/libexec" \
         "$stage/usr/share/applications" "$stage/usr/share/metainfo" \
         "$stage/usr/share/icons/hicolor/scalable/apps" "$stage/usr/share/doc/lastpass-gtk" "$out"

cp -r lastpass_gtk "$stage/usr/lib/lastpass-gtk/"
find "$stage" -name __pycache__ -prune -exec rm -rf {} +
chmod 755 "$stage/usr/lib/lastpass-gtk/lastpass_gtk/askpass.py"
install -m 755 build/lpass-static/lpass "$stage/usr/lib/lastpass-gtk/libexec/lpass"

cat > "$stage/usr/bin/lastpass-gtk" <<'LAUNCH'
#!/bin/sh
export PYTHONPATH="/usr/lib/lastpass-gtk${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -P -m lastpass_gtk "$@"
LAUNCH
chmod 755 "$stage/usr/bin/lastpass-gtk"

install -m 644 data/$appid.desktop "$stage/usr/share/applications/"
install -m 644 data/$appid.metainfo.xml "$stage/usr/share/metainfo/"
install -m 644 data/icons/$appid.svg "$stage/usr/share/icons/hicolor/scalable/apps/"
install -m 644 README.md "$stage/usr/share/doc/lastpass-gtk/"
cat > "$stage/usr/share/doc/lastpass-gtk/copyright" <<'COPY'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: lastpass-gtk
Source: https://github.com/otrajman/lastpass-gtk

Files: *
Copyright: 2026 lastpass-gtk contributors
License: GPL-2.0-or-later

Files: usr/lib/lastpass-gtk/libexec/lpass
Copyright: 2014-2026 LastPass and lastpass-cli contributors
License: GPL-2.0-or-later with OpenSSL exception
Comment: Built from https://github.com/lastpass/lastpass-cli with the patches in
 https://github.com/otrajman/lastpass-gtk/tree/main/patches

License: GPL-2.0-or-later
 On Debian systems the full text is in /usr/share/common-licenses/GPL-2.
COPY

# Shared-library dependencies of the bundled lpass, computed by dpkg.
tmp="$(mktemp -d)"
mkdir -p "$tmp/debian"
printf 'Source: lastpass-gtk\n\nPackage: lastpass-gtk\nArchitecture: any\n' > "$tmp/debian/control"
shlibs="$(cd "$tmp" && dpkg-shlibdeps -O -e "$stage/usr/lib/lastpass-gtk/libexec/lpass" 2>/dev/null \
          | sed -n 's/^shlibs:Depends=//p')"
rm -rf "$tmp"

size="$(du -sk "$stage/usr" | cut -f1)"
cat > "$stage/DEBIAN/control" <<CTRL
Package: lastpass-gtk
Version: $version
Architecture: $arch
Maintainer: lastpass-gtk maintainers <otrajman@users.noreply.github.com>
Installed-Size: $size
Depends: $shlibs, python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5)
Recommends: gir1.2-secret-1
Section: utils
Priority: optional
Homepage: https://github.com/otrajman/lastpass-gtk
Description: unofficial GTK desktop client for LastPass
 A native GNOME (GTK 4 / libadwaita) front end for the official LastPass
 command-line client. Browse, search, copy, add and edit sites and secure
 notes. Bundles a patched build of lastpass-cli; vault crypto and server
 communication are done entirely by lastpass-cli. Not affiliated with LastPass.
CTRL

deb="$out/lastpass-gtk_${version}_${arch}.deb"
fakeroot dpkg-deb --build --root-owner-group -Zxz "$stage" "$deb" >/dev/null
echo "built $deb"
dpkg-deb -f "$deb" Depends
