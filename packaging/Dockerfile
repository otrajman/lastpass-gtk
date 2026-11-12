# Build environment for the .deb and AppImage. Ubuntu 24.04 is the oldest base with
# GTK >= 4.10 and libadwaita >= 1.5, so binaries built here run on 24.04 and newer.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
# Optional package mirror (CI passes GitHub's Azure mirror; archive.ubuntu.com can be
# very slow from runners). Security updates still come from security.ubuntu.com.
ARG UBUNTU_MIRROR=
RUN if [ -n "$UBUNTU_MIRROR" ]; then \
        sed -i "s|http://archive.ubuntu.com/ubuntu/\?|$UBUNTU_MIRROR|g" /etc/apt/sources.list.d/ubuntu.sources; \
    fi \
    && printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' \
        > /etc/apt/apt.conf.d/80-retries
# upgrade first: the packages bundle OpenSSL, libcurl, GTK, etc. from this image, so
# pick up every pending Ubuntu security update at build time.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        build-essential cmake pkg-config patch file git ca-certificates wget \
        libcurl4-openssl-dev libxml2-dev libssl-dev \
        python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0 gir1.2-secret-1 \
        adwaita-icon-theme hicolor-icon-theme librsvg2-common libgdk-pixbuf2.0-bin \
        libglib2.0-bin dconf-gsettings-backend desktop-file-utils appstream \
        dpkg-dev fakeroot patchelf squashfs-tools \
    && rm -rf /var/lib/apt/lists/*
