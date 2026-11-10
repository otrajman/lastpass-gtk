#!/bin/sh
# Build a minimal static libxml2 (no ICU/zlib/lzma/network) for linking into lpass,
# so packaged binaries don't depend on the distro's libxml2 soname (.so.2 vs .so.16).
#   scripts/build-libxml2.sh PREFIX
set -eu
VERSION=2.15.4
SHA256=98087fd181d9070724f3fbc65c7377db03038eb92bd882374daff44940138821
prefix="$1"
[ -f "$prefix/lib/libxml2.a" ] && [ "$(cat "$prefix/.version" 2>/dev/null)" = "$VERSION" ] && exit 0
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
url="https://download.gnome.org/sources/libxml2/${VERSION%.*}/libxml2-$VERSION.tar.xz"
echo "fetching $url"
wget -q -O "$work/x.tar.xz" "$url" 2>/dev/null || curl -fsSL -o "$work/x.tar.xz" "$url"
echo "$SHA256  $work/x.tar.xz" | sha256sum -c - >/dev/null
tar -C "$work" -xJf "$work/x.tar.xz"
cd "$work/libxml2-$VERSION"
./configure --quiet --prefix="$prefix" --disable-shared --enable-static --with-pic \
    --without-python --without-icu --without-lzma --without-zlib --without-http \
    --without-ftp --without-debug --without-history --without-readline >/dev/null
make -s -j"$(nproc)" >/dev/null 2>&1
make -s install >/dev/null
echo "$VERSION" > "$prefix/.version"
echo "static libxml2 $VERSION -> $prefix"
