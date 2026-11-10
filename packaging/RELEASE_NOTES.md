An unofficial, native GNOME desktop client for LastPass, built on the official
[lastpass-cli](https://github.com/lastpass/lastpass-cli). Not affiliated with LastPass.

### Downloads

| File | For |
|---|---|
| `LastPass_GTK-*-x86_64.AppImage` | Any x86_64 distro with glibc ≥ 2.39 (Ubuntu 24.04+, Debian 13+, Fedora 40+, Arch, openSUSE Tumbleweed, …). Self-contained; nothing to install. |
| `lastpass-gtk_*_amd64.deb` | Ubuntu 24.04+ / Debian 13+. Small; uses the system's GTK 4 and libadwaita. |
| `SHA256SUMS` | Checksums for the files above. |

**AppImage:** `chmod +x LastPass_GTK-*.AppImage && ./LastPass_GTK-*.AppImage`

**Debian/Ubuntu:** `sudo apt install ./lastpass-gtk_*_amd64.deb`

Both packages bundle a build of lastpass-cli with two small patches (see `patches/`).
The source for every binary is in this repository and its `third_party/lastpass-cli` submodule.
