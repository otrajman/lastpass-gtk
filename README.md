# LastPass GTK

An unofficial, native GNOME (GTK 4 + libadwaita) desktop app for your LastPass vault.

It's a front end for the official **[lastpass-cli](https://github.com/lastpass/lastpass-cli)**
(`lpass`). All encryption, key derivation and server communication are done by
LastPass's own client code. This app never handles vault crypto itself.
**Not affiliated with or endorsed by LastPass.**

## Install

Grab the latest files from [Releases](../../releases):

- **AppImage (any distro):** self-contained, nothing to install.
  ```sh
  chmod +x LastPass_GTK-*-x86_64.AppImage
  ./LastPass_GTK-*-x86_64.AppImage
  ```
  Needs an x86_64 distro with glibc ≥ 2.39: Ubuntu 24.04+, Debian 13+, Fedora 40+,
  Arch, openSUSE Tumbleweed, and others.

- **Debian / Ubuntu (.deb):** small; uses your system's GTK.
  ```sh
  sudo apt install ./lastpass-gtk_*_amd64.deb
  ```
  Needs Ubuntu 24.04+ or Debian 13+ (libadwaita ≥ 1.5).

## Features

- Log in with master password, multifactor code, or push approval, with an optional "trust this device" setting. If LastPass rejects a login, the app shows the server's reason, such as an email verification request.
- Search the vault (by name, folder, username, or URL) and filter by sites, secure notes, or folder
- View items: reveal or copy the password, copy the username, open the website, and see secure-note fields (Server, Database, …) and notes
- Copying puts the value on the clipboard and clears it after 30 s
- Add, edit, duplicate, and delete sites and secure notes; move items between folders; built-in password generator
- Master-password re-prompt for protected items; save attachments
- Adaptive layout that collapses to a single pane on narrow windows

**Keyboard shortcuts:** Ctrl+F search · Ctrl+N new site · Ctrl+Shift+N new note ·
Ctrl+E edit · Ctrl+Shift+C copy password · Ctrl+B copy username · Ctrl+U open website ·
Delete (in the list) delete · Ctrl+R / F5 sync · Ctrl+Q quit

## How it works

```
GTK UI (app.py) ──► lpass.py ──subprocess──► lpass (lastpass-cli) ──► LastPass servers
      ▲                                          │ needs a secret
      └──── private Unix socket ◄── askpass.py ◄─┘ (LPASS_ASKPASS)
```

- Commands run one at a time on a background thread, so the UI never blocks.
- When `lpass` needs a secret (master password, MFA code, reprompt), it runs
  `askpass.py`. That script forwards the prompt over a per-session Unix socket
  (in `$XDG_RUNTIME_DIR`, mode 0700, with a random token) to the app, which shows
  a dialog. Secrets never go in environment variables or on a command line.
- The `lpass` agent keeps the vault unlocked for its normal timeout (1 hour by
  default; `LPASS_AGENT_TIMEOUT`). After that, the next action asks for the master password again.
- Edits use `lpass edit/add --non-interactive` with the same template `lpass`
  writes for `$EDITOR`. No temp files are created.

### Patches to lastpass-cli

The bundled `lpass` is upstream lastpass-cli (pinned as the `third_party/lastpass-cli`
submodule) plus two small patches in [`patches/`](patches):

1. `0001-askpass-pass-previous-error.patch`: passes the reason a login attempt failed
   to the askpass helper. Upstream discards it, so a GUI can't tell "wrong password"
   apart from "verify this login by email".
2. `0002-honor-CURL_CA_BUNDLE.patch`: lets the relocatable AppImage point libcurl at
   the host's CA bundle, wherever the distro keeps it.

Packaged builds also link a minimal static libxml2. Distros ship incompatible
libxml2 versions (`.so.2` vs `.so.16`), and this avoids depending on either.

## Build from source

Requirements: Python 3.11+, PyGObject, GTK ≥ 4.10, libadwaita ≥ 1.5, and a C toolchain
for lastpass-cli:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
     build-essential cmake pkg-config libcurl4-openssl-dev libxml2-dev libssl-dev
git clone --recursive https://github.com/otrajman/lastpass-gtk.git
cd lastpass-gtk
make lpass          # builds build/lpass/lpass (+ the mock-server lpass-test)
./bin/lastpass-gtk  # run from the checkout
./install.sh        # or install for your user in ~/.local (./install.sh uninstall to remove)
```

### Packages

The `.deb` and AppImage are built inside an Ubuntu 24.04 container, the oldest supported
base, so they run on everything newer. Only Docker (or Podman, with `DOCKER=podman`) is needed:

```sh
make deb        # dist/lastpass-gtk_<ver>_amd64.deb
make appimage   # dist/LastPass_GTK-<ver>-x86_64.AppImage
make dist       # both
```

CI (`.github/workflows/build.yml`) runs the tests and builds both packages on every push.
Pushing a `v*` tag publishes a GitHub release with the packages and `SHA256SUMS`.

## Tests

```sh
make test
```

The parsing and template tests always run. The integration tests run the real
lastpass-cli code against its built-in mock server (`lpass-test`): login, wrong
password and retry with the server's reason, cancel, list, show, edit, and logout.

## Limitations

- Sharing management, password changes, import/export, and attachment upload aren't in the
  GUI. Use `lpass share|passwd|import|export` for those.
- Accounts that sign in through a company identity provider (SSO/federated login) aren't
  supported by lastpass-cli, so they aren't supported here either.
- Custom fields are single-line (an `lpass` template limitation); notes can be multi-line.
- Item icons are generic; there's no favicon fetching (it would leak your site list).
- Logging out deletes the local encrypted vault cache, the same as `lpass logout`.
- x86_64 packages only for now.

## License

GPL-2.0-or-later (see [LICENSE](LICENSE)). The bundled lastpass-cli is GPL-2.0-or-later
with an OpenSSL linking exception; see `third_party/lastpass-cli`.
