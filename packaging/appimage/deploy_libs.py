#!/usr/bin/env python3
"""Copy the shared-library closure of every ELF file in an AppDir into usr/lib and
point each ELF's RUNPATH at it, so the AppImage needs no LD_LIBRARY_PATH (which
would leak into processes the app launches, such as the user's web browser).

Libraries that must come from the host (glibc, the GL/driver stack, X11,
fontconfig, the C++ runtime that host GPU drivers link against) are not bundled.

Libraries in FALLBACK are copied to usr/lib/fallback instead: the host's copy is
preferred (it must match the host's GPU drivers), and AppRun links in ours only for
the ones a host lacks.

usage: deploy_libs.py APPDIR
"""
import os
import re
import shutil
import subprocess
import sys

EXCLUDE = re.compile(r"^(" + "|".join([
    r"ld-linux.*", r"libc\.so.*", r"libm\.so.*", r"libdl\.so.*", r"libpthread\.so.*",
    r"librt\.so.*", r"libresolv\.so.*", r"libutil\.so.*", r"libanl\.so.*", r"libnsl\.so.*",
    r"libBrokenLocale\.so.*", r"libmvec\.so.*",
    r"libGL\.so.*", r"libGLX\.so.*", r"libEGL\.so.*", r"libGLdispatch\.so.*", r"libOpenGL\.so.*",
    r"libGLESv2\.so.*", r"libgbm\.so.*", r"libdrm\.so.*",
    r"libX11\.so.*", r"libX11-xcb\.so.*", r"libxcb\.so.*", r"libxcb-.*",
    r"libfontconfig\.so.*", r"libfreetype\.so.*",
    r"libstdc\+\+\.so.*", r"libgcc_s\.so.*", r"libasound\.so.*",
]) + r")$")


FALLBACK = re.compile(r"^(libvulkan\.so\.1|libwayland-(client|egl|cursor)\.so\.\d+)$")


def is_elf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def needed(path):
    out = subprocess.run(["ldd", path], capture_output=True, text=True).stdout
    deps = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\S+) => (/\S+)", line)
        if m:
            deps[m.group(1)] = m.group(2)
        elif "not found" in line:
            sys.exit(f"{path}: unresolved dependency: {line.strip()}")
    return deps


def main(appdir):
    libdir = os.path.join(appdir, "usr", "lib")
    fbdir = os.path.join(libdir, "fallback")
    os.makedirs(fbdir, exist_ok=True)
    seen = set()
    queue = [os.path.join(dp, f) for dp, _, fs in os.walk(appdir) for f in fs
             if not os.path.islink(os.path.join(dp, f)) and is_elf(os.path.join(dp, f))]
    elves = list(queue)
    while queue:
        path = queue.pop()
        for soname, src in needed(path).items():
            if soname in seen or EXCLUDE.match(soname):
                continue
            seen.add(soname)
            dst = os.path.join(fbdir if FALLBACK.match(soname) else libdir, soname)
            if not os.path.exists(dst):
                shutil.copy2(os.path.realpath(src), dst)
                os.chmod(dst, 0o755)
                elves.append(dst)
                queue.append(dst)
    for path in elves:
        rel = os.path.relpath(libdir, os.path.dirname(path))
        rpath = "$ORIGIN" if rel == "." else f"$ORIGIN/{rel}"
        if os.path.dirname(path) == fbdir:
            rpath = "$ORIGIN:$ORIGIN/.."
        subprocess.run(["patchelf", "--set-rpath", rpath, path], check=True)
    print(f"bundled {len(seen)} libraries, patched {len(elves)} ELF files")


if __name__ == "__main__":
    main(sys.argv[1])
