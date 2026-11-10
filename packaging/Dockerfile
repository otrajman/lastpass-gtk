# Build environment for the .deb and AppImage. Ubuntu 24.04 is the oldest base with
# GTK >= 4.10 and libadwaita >= 1.5, so binaries built here run on 24.04 and newer.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake pkg-config patch file git ca-certificates wget \
        libcurl4-openssl-dev libxml2-dev libssl-dev \
        python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0 \
        adwaita-icon-theme hicolor-icon-theme librsvg2-common libgdk-pixbuf2.0-bin \
        libglib2.0-bin dconf-gsettings-backend desktop-file-utils appstream \
        dpkg-dev fakeroot patchelf squashfs-tools \
    && rm -rf /var/lib/apt/lists/*
