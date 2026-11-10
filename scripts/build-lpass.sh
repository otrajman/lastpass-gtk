#!/bin/sh
# Build lpass from the pinned lastpass-cli submodule plus our patches.
#
#   scripts/build-lpass.sh [OUTDIR]     -> OUTDIR/lpass   (default: build/lpass)
#   LPASS_TEST=1 scripts/build-lpass.sh -> also OUTDIR/lpass-test (mock server build)
#   STATIC_LIBXML2=1 ...                 -> link a minimal static libxml2 (used for packages)
#
# Needs: cmake, a C compiler, pkg-config, and dev headers for libcurl, libxml2, openssl.
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$root/build/lpass}"
mkdir -p "$out" && out="$(cd "$out" && pwd)"
src="$root/third_party/lastpass-cli"

if [ ! -f "$src/CMakeLists.txt" ]; then
    git -C "$root" submodule update --init third_party/lastpass-cli
fi

work="$out/src"
rm -rf "$work" "$out/cmake"
mkdir -p "$work"
# Copy the pristine sources (never patch the submodule in place).
tar -C "$src" --exclude=.git --exclude=build -cf - . | tar -C "$work" -xf -
for p in "$root"/patches/*.patch; do
    echo "applying $(basename "$p")"
    patch -s -d "$work" -p1 < "$p"
done

set --
if [ -n "${STATIC_LIBXML2:-}" ]; then
    "$root/scripts/build-libxml2.sh" "$out/libxml2"
    set -- -DLIBXML2_LIBRARY="$out/libxml2/lib/libxml2.a" \
           -DLIBXML2_INCLUDE_DIR="$out/libxml2/include/libxml2" \
           -DCMAKE_C_STANDARD_LIBRARIES="-lm"
fi
cmake -S "$work" -B "$out/cmake" -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -Wno-dev "$@" >/dev/null
targets="lpass"
[ -n "${LPASS_TEST:-}" ] && targets="$targets lpass-test"
# shellcheck disable=SC2086
make -s -C "$out/cmake" -j"$(nproc)" $targets 2>&1 | grep -v -e "warning:" -e "note:" -e "^ *[0-9|]" -e "^ *|" || true
for t in $targets; do
    install -m 755 "$out/cmake/$t" "$out/$t"
done
strip "$out/lpass"
"$out/lpass" --version
