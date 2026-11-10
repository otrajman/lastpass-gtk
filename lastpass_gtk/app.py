"""GTK4 / libadwaita front end for the LastPass CLI."""
from __future__ import annotations

import json
import os
import queue
import re
import secrets
import string
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .lpass import Detail, Entry, Lpass, LpassError, build_template  # noqa: E402

APP_ID = "io.github.otrajman.LastPassGtk"
APP_NAME = "LastPass GTK"
VERSION = "0.1.1"
CLIPBOARD_CLEAR_SECONDS = 30
STATE_FILE = Path(GLib.get_user_config_dir()) / "lastpass-gtk" / "state.json"
SECRET_FIELD = re.compile(r"pass|pin|secret|key|token|code|cvv|cvc|security", re.I)

ALL, SITES, NOTES = 0, 1, 2  # fixed entries at the top of the filter dropdown


# --------------------------------------------------------------------------- helpers

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    except OSError:
        pass


def generate_password(length: int = 20, symbols: bool = True) -> str:
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits]
    if symbols:
        pools.append("!@#$%^&*()-_=+[]{};:,.?/")
    alphabet = "".join(pools)
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if length < len(pools) or all(any(c in p for c in pw) for p in pools):
            return pw


def host_of(url: str) -> str:
    try:
        return urlparse(url if "://" in url else "https://" + url).hostname or url
    except ValueError:
        return url


def host_environment() -> dict:
    """The environment as it was before the AppImage's AppRun changed it.

    AppRun records each variable it overrides as LPGTK_ORIG_<NAME> ("__unset__" if
    it wasn't set), so processes we launch for the user see their normal setup.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("LPGTK_ORIG_")}
    for k, v in os.environ.items():
        if k.startswith("LPGTK_ORIG_"):
            name = k[len("LPGTK_ORIG_"):]
            if v == "__unset__":
                env.pop(name, None)
            else:
                env[name] = v
    return env


def make_spinner(**props) -> Gtk.Widget:
    """Adw.Spinner needs libadwaita 1.6; fall back to Gtk.Spinner on 1.5 (Ubuntu 24.04)."""
    if hasattr(Adw, "Spinner"):
        return Adw.Spinner(**props)
    return Gtk.Spinner(spinning=True, **props)


def icon_button(icon: str, tooltip: str, cb: Callable, *args) -> Gtk.Button:
    b = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    b.add_css_class("flat")
    b.connect("clicked", lambda *_: cb(*args))
    return b


class Worker:
    """Runs lpass calls one at a time off the UI thread (lpass shares one blob file)."""

    def __init__(self, on_busy: Callable[[bool], None]):
        self._q: queue.Queue = queue.Queue()
        self._pending = 0
        self._on_busy = on_busy
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, fn: Callable, ok: Callable | None = None, err: Callable | None = None):
        self._pending += 1
        self._on_busy(True)
        self._q.put((fn, ok, err))

    def _loop(self):
        while True:
            fn, ok, err = self._q.get()
            try:
                res, exc = fn(), None
            except Exception as e:  # noqa: BLE001 - surfaced to the UI
                res, exc = None, e
            GLib.idle_add(self._done, ok, err, res, exc)

    def _done(self, ok, err, res, exc):
        self._pending -= 1
        self._on_busy(self._pending > 0)
        if exc is None:
            if ok:
                ok(res)
        elif err:
            err(exc)
        return False


# --------------------------------------------------------------------------- edit dialog

class EditDialog(Adw.Dialog):
    def __init__(self, *, is_note: bool, entry: Entry | None, detail: Detail | None,
                 on_save: Callable[[str, str], None]):
        super().__init__()
        self._is_note = is_note
        self._on_save = on_save
        self._detail = detail
        kind = "Secure Note" if is_note else "Site"
        self.set_title(f"Edit {kind}" if entry else f"New {kind}")
        self.set_content_width(520)
        self.set_content_height(680)

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self._save = Gtk.Button(label="Save")
        self._save.add_css_class("suggested-action")
        self._save.connect("clicked", self._on_save_clicked)
        hb.pack_start(cancel)
        hb.pack_end(self._save)
        tv.add_top_bar(hb)

        page = Adw.PreferencesPage()
        tv.set_content(page)
        self.set_child(tv)

        g = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title="Name", text=entry.name if entry else "")
        self.folder_row = Adw.EntryRow(title="Folder", text=entry.folder if entry else "")
        self.name_row.connect("changed", self._validate)
        g.add(self.name_row)
        g.add(self.folder_row)
        page.add(g)

        d = detail or Detail("0")
        self.url_row = self.user_row = self.pw_row = None
        if not is_note or d.url or d.username or d.password:
            g = Adw.PreferencesGroup(title="Login" if not is_note else "")
            if not is_note or d.url:
                self.url_row = Adw.EntryRow(title="URL", text=d.url or "")
                self.url_row.set_input_purpose(Gtk.InputPurpose.URL)
                g.add(self.url_row)
            if not is_note or d.username:
                self.user_row = Adw.EntryRow(title="Username", text=d.username)
                g.add(self.user_row)
            if not is_note or d.password:
                self.pw_row = Adw.PasswordEntryRow(title="Password", text=d.password)
                self.pw_row.add_suffix(self._generator_button())
                g.add(self.pw_row)
            page.add(g)

        # custom / note-type fields (NoteType is preserved but not editable)
        self._hidden_fields = [(k, v) for k, v in d.fields if k == "NoteType"]
        self.field_rows: list[tuple[str, Adw.EntryRow]] = []
        visible = [(k, v) for k, v in d.fields if k != "NoteType"]
        if visible:
            g = Adw.PreferencesGroup(title="Fields")
            for k, v in visible:
                cls = Adw.PasswordEntryRow if SECRET_FIELD.search(k) else Adw.EntryRow
                row = cls(title=k, text=v)
                g.add(row)
                self.field_rows.append((k, row))
            page.add(g)

        g = Adw.PreferencesGroup()
        self.reprompt_row = Adw.SwitchRow(title="Require master password",
                                          subtitle="Re-prompt before showing this item",
                                          active=d.reprompt)
        g.add(self.reprompt_row)
        page.add(g)

        g = Adw.PreferencesGroup(title="Notes")
        self.notes = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8,
                                  bottom_margin=8, left_margin=8, right_margin=8)
        self.notes.get_buffer().set_text(d.notes)
        sw = Gtk.ScrolledWindow(child=self.notes, min_content_height=140)
        frame = Gtk.Frame(child=sw)
        frame.add_css_class("card")
        g.add(frame)
        page.add(g)

        self._validate()
        GLib.idle_add(self.name_row.grab_focus)

    def _generator_button(self) -> Gtk.MenuButton:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        lbox = Gtk.Box(spacing=8)
        lbox.append(Gtk.Label(label="Length", hexpand=True, xalign=0))
        length = Gtk.SpinButton.new_with_range(8, 128, 1)
        length.set_value(20)
        lbox.append(length)
        box.append(lbox)
        symbols = Gtk.CheckButton(label="Include symbols", active=True)
        box.append(symbols)
        go = Gtk.Button(label="Generate")
        go.add_css_class("suggested-action")
        box.append(go)
        pop = Gtk.Popover(child=box)
        btn = Gtk.MenuButton(icon_name="view-refresh-symbolic", popover=pop,
                             tooltip_text="Generate password", valign=Gtk.Align.CENTER)
        btn.add_css_class("flat")

        def gen(*_):
            self.pw_row.set_text(generate_password(int(length.get_value()), symbols.get_active()))
            pop.popdown()
        go.connect("clicked", gen)
        return btn

    def _validate(self, *_):
        self._save.set_sensitive(bool(self.name_row.get_text().strip()))

    def _on_save_clicked(self, *_):
        name = self.name_row.get_text().strip().strip("/")
        folder = self.folder_row.get_text().strip().strip("/")
        fullname = f"{folder}/{name}" if folder else name
        buf = self.notes.get_buffer()
        notes = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        fields = self._hidden_fields + [(k, r.get_text()) for k, r in self.field_rows]
        template = build_template(
            fullname,
            url=self.url_row.get_text() if self.url_row else "",
            username=self.user_row.get_text() if self.user_row else "",
            password=self.pw_row.get_text() if self.pw_row else "",
            fields=fields, notes=notes, reprompt=self.reprompt_row.get_active(),
            is_note=self._is_note,
        )
        self._on_save(fullname, template)
        self.close()


# --------------------------------------------------------------------------- main window

class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1000, 680)
        self.set_size_request(360, 480)
        self.state = load_state()
        self.entries: list[Entry] = []
        self.current: Optional[Entry] = None
        self.current_detail: Optional[Detail] = None
        self._clip_serial = 0
        self.lp: Optional[Lpass] = None

        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.stack)
        self.set_content(self.toasts)
        self._install_actions()  # pages below toggle these actions

        self.stack.add_named(self._build_loading(), "loading")
        self.stack.add_named(self._build_login(), "login")
        self.stack.add_named(self._build_vault(), "vault")
        self.error_page = Adw.StatusPage(icon_name="dialog-error-symbolic",
                                         title="lpass not found")
        self.stack.add_named(self.error_page, "error")
        self.stack.set_visible_child_name("loading")

        self.worker = Worker(self._set_busy)

        try:
            self.lp = Lpass(self._prompt_from_thread)
        except (FileNotFoundError, OSError) as e:
            self.error_page.set_description(str(e))
            self.stack.set_visible_child_name("error")
            return
        self.connect("close-request", self._on_close)
        self.worker.submit(self.lp.status, self._after_status,
                           lambda e: self._show_login())

    # ---- pages ---------------------------------------------------------------
    def _build_loading(self):
        page = Adw.StatusPage(title="Opening vault…")
        page.set_child(make_spinner(width_request=32, height_request=32))
        tv = Adw.ToolbarView(content=page)
        tv.add_top_bar(Adw.HeaderBar(show_title=False))
        return tv

    def _build_login(self):
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar(show_title=False))
        page = Adw.StatusPage(icon_name="dialog-password-symbolic", title="LastPass",
                              description="Sign in to your vault")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        group = Adw.PreferencesGroup()
        self.email_row = Adw.EntryRow(title="Email", text=self.state.get("username", ""))
        self.email_row.set_input_purpose(Gtk.InputPurpose.EMAIL)
        self.email_row.connect("entry-activated", self._do_login)
        self.trust_row = Adw.SwitchRow(title="Trust this device",
                                       subtitle="Skip multifactor on this computer for 30 days")
        group.add(self.email_row)
        group.add(self.trust_row)
        box.append(group)
        self.login_btn = Gtk.Button(label="Log In", halign=Gtk.Align.CENTER)
        self.login_btn.add_css_class("pill")
        self.login_btn.add_css_class("suggested-action")
        self.login_btn.connect("clicked", self._do_login)
        box.append(self.login_btn)
        self.login_status = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, visible=False)
        self.login_status.append(make_spinner())
        lbl = Gtk.Label(label="Contacting LastPass… approve the sign-in on your "
                              "device if you use push multifactor.", wrap=True)
        lbl.add_css_class("dim-label")
        self.login_status.append(lbl)
        box.append(self.login_status)
        page.set_child(Adw.Clamp(maximum_size=420, child=box))
        tv.set_content(page)
        return tv

    def _build_vault(self):
        self.split = Adw.NavigationSplitView(min_sidebar_width=300, max_sidebar_width=420,
                                             sidebar_width_fraction=0.36)

        # --- sidebar
        stv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.vault_title = Adw.WindowTitle(title="Vault")
        hb.set_title_widget(self.vault_title)
        add_menu = Gio.Menu()
        add_menu.append("New Site", "win.new-site")
        add_menu.append("New Secure Note", "win.new-note")
        hb.pack_start(Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=add_menu,
                                     tooltip_text="Add item"))
        main_menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("Sync Now", "win.sync")
        main_menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Keyboard Shortcuts", "win.show-help-overlay")
        sec.append(f"About {APP_NAME}", "win.about")
        main_menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Log Out", "win.logout")
        main_menu.append_section(None, sec)
        hb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=main_menu,
                                   primary=True, tooltip_text="Main menu"))
        self.spinner = make_spinner(visible=False)
        hb.pack_end(self.spinner)
        stv.add_top_bar(hb)

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_start=10, margin_end=10, margin_bottom=6)
        self.search = Gtk.SearchEntry(placeholder_text="Search vault")
        self.search.connect("search-changed", lambda *_: self._refilter())
        self.search.connect("activate", self._activate_first)
        self.search.connect("stop-search", lambda *_: self.search.set_text(""))
        top.append(self.search)
        self.filter_model = Gtk.StringList.new(["All items", "Sites", "Secure notes"])
        self.filter_dd = Gtk.DropDown(model=self.filter_model)
        self.filter_dd.connect("notify::selected", lambda *_: self._refilter())
        top.append(self.filter_dd)
        stv.add_top_bar(top)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.set_filter_func(self._filter_row)
        self.listbox.set_header_func(self._row_header)
        self.listbox.connect("row-selected", self._on_row_selected)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_list_key)
        self.listbox.add_controller(keys)
        empty = Adw.StatusPage(icon_name="system-search-symbolic", title="No items")
        empty.add_css_class("compact")
        self.listbox.set_placeholder(empty)
        stv.set_content(Gtk.ScrolledWindow(child=self.listbox, vexpand=True,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.split.set_sidebar(Adw.NavigationPage(title="Vault", child=stv))

        # --- content
        self.content_tv = Adw.ToolbarView()
        chb = Adw.HeaderBar(show_title=False)
        self.edit_btn = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Edit",
                                   action_name="win.edit")
        item_menu = Gio.Menu()
        item_menu.append("Duplicate", "win.duplicate")
        item_menu.append("Delete…", "win.delete")
        self.item_menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic",
                                            menu_model=item_menu, tooltip_text="More")
        chb.pack_end(self.item_menu_btn)
        chb.pack_end(self.edit_btn)
        self.content_tv.add_top_bar(chb)
        self.content_page = Adw.NavigationPage(title="Details", child=self.content_tv)
        self.split.set_content(self.content_page)
        self._show_placeholder()

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)
        return self.split

    # ---- actions -------------------------------------------------------------
    def _install_actions(self):
        def add(name, cb):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_: cb())
            self.add_action(a)
            return a
        add("new-site", lambda: self._open_editor(is_note=False))
        add("new-note", lambda: self._open_editor(is_note=True))
        add("sync", self._sync)
        add("logout", self._confirm_logout)
        add("about", self._about)
        add("search", self._focus_search)
        self.act_edit = add("edit", self._edit_current)
        self.act_delete = add("delete", self._confirm_delete)
        self.act_dup = add("duplicate", self._duplicate_current)
        self.act_copy_pw = add("copy-password", lambda: self._quick_copy(self.current, "password"))
        self.act_copy_user = add("copy-username", lambda: self._quick_copy(self.current, "username"))
        self.act_open_url = add("open-url", self._open_current_url)
        add("show-help-overlay", self._shortcuts)
        self._set_item_actions(False)

        app = self.get_application()
        for action, accels in {
            "win.search": ["<Ctrl>f"], "win.new-site": ["<Ctrl>n"],
            "win.new-note": ["<Ctrl><Shift>n"], "win.sync": ["<Ctrl>r", "F5"],
            "win.edit": ["<Ctrl>e"], "win.copy-password": ["<Ctrl><Shift>c"],
            "win.copy-username": ["<Ctrl>b"], "win.open-url": ["<Ctrl>u"],
            "win.show-help-overlay": ["<Ctrl>question"],
            "app.quit": ["<Ctrl>q"],
        }.items():
            app.set_accels_for_action(action, accels)

    def _set_item_actions(self, on: bool):
        for a in (self.act_edit, self.act_delete, self.act_dup, self.act_copy_pw,
                  self.act_copy_user, self.act_open_url):
            a.set_enabled(on)

    # ---- busy / errors / toasts ---------------------------------------------
    def _set_busy(self, busy: bool):
        self.spinner.set_visible(busy)

    def toast(self, text: str, timeout: int = 3):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout))

    def _error(self, exc: Exception, what: str = ""):
        if isinstance(exc, LpassError):
            if exc.cancelled:
                self.toast("Cancelled")
                return
            msg = exc.message
            if "not logged in" in msg.lower() or "could not find decryption key" in msg.lower():
                self._show_login()
        else:
            msg = str(exc)
        dlg = Adw.AlertDialog(heading=what or "Something went wrong", body=msg)
        dlg.add_response("ok", "OK")
        dlg.present(self)

    # ---- secret prompts from lpass (via askpass bridge) -----------------------
    def _prompt_from_thread(self, label: str, attempt: int = 1, error: str = "") -> Optional[str]:
        ev = threading.Event()
        box: dict = {}
        def show():
            try:
                self._prompt_dialog(label, attempt, error, ev, box)
            except Exception:  # never leave lpass (and the worker) hanging
                ev.set()
                raise
            return False
        GLib.idle_add(show)
        ev.wait()
        return box.get("answer")

    def _prompt_dialog(self, label: str, attempt: int, error: str, ev: threading.Event, box: dict):
        low = label.lower()
        if "code" in low:
            heading, body = "Multifactor Code", "Enter the code from your authenticator app."
            if error:
                body = error
            elif attempt > 1:
                body = "That code was not accepted. Please try again."
            entry = Gtk.Entry(input_purpose=Gtk.InputPurpose.DIGITS)
        else:
            heading = label
            user = self.state.get("username", "")
            body = (f"Enter the master password for {user}." if user
                    else "Enter your master password.")
            # lpass re-prompts after *any* login failure (email verification,
            # SSO account, rate limiting, ...), so show the server's reason.
            if error:
                body = f"LastPass said: {error}"
            elif attempt > 1:
                body = "Login failed. Check your master password and try again."
            entry = Gtk.PasswordEntry(show_peek_icon=True)
        entry.set_property("activates-default", True)
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Unlock" if "password" in low else "Continue")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.set_close_response("cancel")

        def on_response(_d, resp):
            if resp == "ok":
                box["answer"] = entry.get_text()
            ev.set()
        dlg.connect("response", on_response)
        dlg.present(self)
        entry.grab_focus()
        return False

    # ---- login flow ------------------------------------------------------------
    def _after_status(self, user: Optional[str]):
        if user:
            self.state["username"] = user
            self._load_vault()
        else:
            self._show_login()

    def _show_login(self):
        self.login_btn.set_sensitive(True)
        self.login_status.set_visible(False)
        self.stack.set_visible_child_name("login")
        self.email_row.grab_focus()

    def _do_login(self, *_):
        user = self.email_row.get_text().strip()
        if not user:
            self.email_row.add_css_class("error")
            return
        self.email_row.remove_css_class("error")
        self.state["username"] = user
        save_state(self.state)
        self.login_btn.set_sensitive(False)
        self.login_status.set_visible(True)
        trust = self.trust_row.get_active()

        def fail(e):
            self.login_btn.set_sensitive(True)
            self.login_status.set_visible(False)
            self._error(e, "Login failed")
        self.worker.submit(lambda: self.lp.login(user, trust), lambda _: self._load_vault(), fail)

    def _confirm_logout(self):
        dlg = Adw.AlertDialog(heading="Log Out?",
                              body="The local copy of your vault will be removed from this computer.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("logout", "Log Out")
        dlg.set_response_appearance("logout", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "logout" and self._logout())
        dlg.present(self)

    def _logout(self):
        def done(_):
            self.entries = []
            self._populate()
            self._show_placeholder()
            self._show_login()
        self.worker.submit(self.lp.logout, done, lambda e: (self._error(e, "Logout failed"), done(None)))

    # ---- vault list ------------------------------------------------------------
    def _load_vault(self, sync: str = "auto", select_id: Optional[str] = None):
        if self.stack.get_visible_child_name() != "vault":
            self.stack.set_visible_child_name("loading")

        def ok(entries):
            self.entries = entries
            self.vault_title.set_subtitle(self.state.get("username", ""))
            self._populate(select_id or (self.current.id if self.current else None))
            self.stack.set_visible_child_name("vault")
            if sync == "now":
                self.toast(f"Synced — {len(entries)} items")

        def err(e):
            if self.stack.get_visible_child_name() != "vault":
                self._show_login()
            self._error(e, "Could not load vault")
        self.worker.submit(lambda: self.lp.list_entries(sync=sync), ok, err)

    def _sync(self):
        if self.stack.get_visible_child_name() == "vault":
            self._load_vault(sync="now")

    def _populate(self, select_id: Optional[str] = None):
        self.listbox.remove_all()
        entries = sorted(self.entries, key=lambda e: (e.folder.lower(), e.name.lower()))
        folders = sorted({e.folder for e in entries if e.folder}, key=str.lower)
        # rebuild folder list in the filter dropdown, keeping selection if possible
        prev = self.filter_dd.get_selected()
        prev_name = self.filter_model.get_string(prev) if prev != Gtk.INVALID_LIST_POSITION else None
        self.filter_model.splice(3, self.filter_model.get_n_items() - 3, folders)
        if prev_name in folders:
            self.filter_dd.set_selected(3 + folders.index(prev_name))
        elif prev is not None and prev > NOTES:
            self.filter_dd.set_selected(ALL)

        to_select = None
        for e in entries:
            row = self._make_row(e)
            self.listbox.append(row)
            if e.id == select_id:
                to_select = row
        if to_select:
            self.listbox.select_row(to_select)
        elif self.current and not any(e.id == self.current.id for e in entries):
            self._show_placeholder()

    def _make_row(self, e: Entry) -> Gtk.ListBoxRow:
        row = Adw.ActionRow(title=e.name or "(unnamed)", use_markup=False, activatable=True)
        row.set_title_lines(1)
        sub = e.username or ("" if e.is_note else host_of(e.url))
        if sub:
            row.set_subtitle(sub)
            row.set_subtitle_lines(1)
        icon = "accessories-text-editor-symbolic" if e.is_note else "web-browser-symbolic"
        row.add_prefix(Gtk.Image(icon_name=icon))
        if not e.is_note:
            b = icon_button("edit-copy-symbolic", "Copy password", self._quick_copy, e, "password")
            row.add_suffix(b)
        row.entry = e
        return row

    def _filter_row(self, row) -> bool:
        e: Entry = row.entry
        sel = self.filter_dd.get_selected()
        if sel == SITES and e.is_note:
            return False
        if sel == NOTES and not e.is_note:
            return False
        if sel > NOTES:
            folder = self.filter_model.get_string(sel)
            if not (e.folder == folder or e.folder.startswith(folder + "/")):
                return False
        q = self.search.get_text().strip().lower()
        if not q:
            return True
        hay = f"{e.fullname}\n{e.username}\n{'' if e.is_note else e.url}".lower()
        return all(tok in hay for tok in q.split())

    def _row_header(self, row, before):
        folder = row.entry.folder
        if before is not None and before.entry.folder == folder:
            row.set_header(None)
            return
        lbl = Gtk.Label(label=folder or "No folder", xalign=0,
                        margin_top=12 if before else 4, margin_bottom=4, margin_start=12)
        lbl.add_css_class("heading")
        lbl.add_css_class("dim-label")
        row.set_header(lbl)

    def _refilter(self):
        self.listbox.invalidate_filter()
        self.listbox.invalidate_headers()

    def _activate_first(self, *_):
        i = 0
        while (row := self.listbox.get_row_at_index(i)) is not None:
            if row.get_child_visible() and self._filter_row(row):
                self.listbox.select_row(row)
                row.grab_focus()
                return
            i += 1

    def _on_list_key(self, _ctl, keyval, _code, state):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and self.act_delete.get_enabled():
            self._confirm_delete()
            return True
        return False

    def _focus_search(self):
        if self.stack.get_visible_child_name() == "vault":
            self.split.set_show_content(False)
            self.search.grab_focus()

    # ---- detail pane -----------------------------------------------------------
    def _show_placeholder(self):
        self.current = None
        self.current_detail = None
        self._set_item_actions(False)
        self.edit_btn.set_visible(False)
        self.item_menu_btn.set_visible(False)
        self.content_page.set_title("Details")
        page = Adw.StatusPage(icon_name="dialog-password-symbolic", title="No item selected",
                              description="Choose an item from the list, or press Ctrl+N to add one.")
        self.content_tv.set_content(page)

    def _on_row_selected(self, _lb, row):
        if row is None:
            return
        e: Entry = row.entry
        self.current = e
        self.current_detail = None
        self._set_item_actions(False)
        self.content_page.set_title(e.name)
        spin = Adw.StatusPage(title=e.name)
        spin.set_child(make_spinner(width_request=32, height_request=32))
        self.content_tv.set_content(spin)
        self.split.set_show_content(True)

        def ok(detail: Detail):
            if self.current is not e:
                return
            self.current_detail = detail
            self._render_detail(e, detail)

        def err(exc):
            if self.current is not e:
                return
            if isinstance(exc, LpassError) and exc.cancelled:
                page = Adw.StatusPage(icon_name="system-lock-screen-symbolic", title="Locked",
                                      description="This item requires your master password.")
                retry = Gtk.Button(label="Unlock", halign=Gtk.Align.CENTER)
                retry.add_css_class("pill")
                retry.connect("clicked", lambda *_: self._on_row_selected(None, row))
                page.set_child(retry)
                self.content_tv.set_content(page)
            else:
                self._show_placeholder()
                self._error(exc, "Could not open item")
        self.worker.submit(lambda: self.lp.show(e.id), ok, err)

    def _render_detail(self, e: Entry, d: Detail):
        self._set_item_actions(True)
        self.act_copy_pw.set_enabled(bool(d.password))
        self.act_copy_user.set_enabled(bool(d.username))
        self.act_open_url.set_enabled(bool(d.url) and not e.is_note)
        self.edit_btn.set_visible(True)
        self.item_menu_btn.set_visible(True)

        page = Adw.PreferencesPage()
        head = Adw.PreferencesGroup()
        hbox = Gtk.Box(spacing=14)
        img = Gtk.Image(icon_name="accessories-text-editor-symbolic" if e.is_note
                        else "web-browser-symbolic", pixel_size=48)
        img.add_css_class("dim-label")
        hbox.append(img)
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        t = Gtk.Label(label=e.name, xalign=0, wrap=True, selectable=True)
        t.add_css_class("title-2")
        vb.append(t)
        sub = " / ".join(p for p in e.folder.split("/") if p) or "No folder"
        if d.reprompt:
            sub += "  ·  protected"
        s = Gtk.Label(label=sub, xalign=0)
        s.add_css_class("dim-label")
        vb.append(s)
        hbox.append(vb)
        head.add(hbox)
        page.add(head)

        main = Adw.PreferencesGroup()
        if d.username:
            main.add(self._value_row("Username", d.username))
        if d.password:
            main.add(self._value_row("Password", d.password, secret=True))
        if d.url and not e.is_note:
            main.add(self._value_row("Website", d.url, url=True))
        if d.application:
            main.add(self._value_row("Application", d.application))
        if main.get_first_child() is not None and any((d.username, d.password, d.url, d.application)):
            page.add(main)

        fields = [(k, v) for k, v in d.fields if k != "NoteType"]
        if fields:
            notetype = dict(d.fields).get("NoteType", "")
            g = Adw.PreferencesGroup(title=notetype or "Fields")
            for k, v in fields:
                g.add(self._value_row(k, v, secret=bool(SECRET_FIELD.search(k))))
            page.add(g)

        if d.notes:
            g = Adw.PreferencesGroup(title="Notes")
            g.set_header_suffix(icon_button("edit-copy-symbolic", "Copy notes",
                                            self.copy, d.notes, "Notes"))
            lbl = Gtk.Label(label=d.notes, xalign=0, yalign=0, wrap=True, selectable=True,
                            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
            lbl.set_wrap_mode(2)  # Pango.WrapMode.WORD_CHAR
            frame = Gtk.Frame(child=lbl)
            frame.add_css_class("card")
            g.add(frame)
            page.add(g)

        if d.attachments:
            g = Adw.PreferencesGroup(title="Attachments")
            for att_id, fname in d.attachments:
                row = Adw.ActionRow(title=fname, use_markup=False)
                row.add_prefix(Gtk.Image(icon_name="mail-attachment-symbolic"))
                row.add_suffix(icon_button("document-save-symbolic", "Save…",
                                           self._save_attachment, e, att_id, fname))
                g.add(row)
            page.add(g)

        meta = []
        if e.last_modified and e.last_modified != "skipped":
            meta.append(f"Modified {e.last_modified} GMT")
        meta.append(f"ID {e.id}")
        foot = Gtk.Label(label="  ·  ".join(meta), margin_top=6)
        foot.add_css_class("dim-label")
        foot.add_css_class("caption")
        fg = Adw.PreferencesGroup()
        fg.add(foot)
        page.add(fg)

        self.content_tv.set_content(page)

    def _value_row(self, title: str, value: str, secret: bool = False, url: bool = False):
        row = Adw.ActionRow(title=title, use_markup=False, subtitle_selectable=not secret)
        row.add_css_class("property")
        masked = "●" * min(max(len(value), 8), 24)
        row.set_subtitle(masked if secret else value)
        if secret:
            reveal = Gtk.ToggleButton(icon_name="view-reveal-symbolic", tooltip_text="Show",
                                      valign=Gtk.Align.CENTER)
            reveal.add_css_class("flat")

            def toggled(btn):
                on = btn.get_active()
                row.set_subtitle(value if on else masked)
                row.set_subtitle_selectable(on)
                btn.set_icon_name("view-conceal-symbolic" if on else "view-reveal-symbolic")
                btn.set_tooltip_text("Hide" if on else "Show")
            reveal.connect("toggled", toggled)
            row.add_suffix(reveal)
        if url:
            row.add_suffix(icon_button("adw-external-link-symbolic", "Open in browser",
                                       self._open_url, value))
        row.add_suffix(icon_button("edit-copy-symbolic", f"Copy {title.lower()}",
                                   self.copy, value, title))
        return row

    # ---- clipboard / urls / attachments ------------------------------------------
    def copy(self, text: str, what: str = "Text"):
        clip = self.get_clipboard()
        clip.set(text)
        self._clip_serial += 1
        serial = self._clip_serial

        def clear():
            if serial == self._clip_serial:
                clip.set("")
            return False
        GLib.timeout_add_seconds(CLIPBOARD_CLEAR_SECONDS, clear)
        self.toast(f"{what} copied — clipboard clears in {CLIPBOARD_CLEAR_SECONDS}s")

    def _quick_copy(self, e: Optional[Entry], which: str):
        if e is None:
            return
        d = self.current_detail
        if d is not None and self.current is e:
            value = getattr(d, which)
            if value:
                self.copy(value, which.capitalize())
            return

        def ok(detail: Detail):
            value = getattr(detail, which)
            if value:
                self.copy(value, which.capitalize())
            else:
                self.toast(f"{e.name} has no {which}")
        self.worker.submit(lambda: self.lp.show(e.id), ok, lambda x: self._error(x, "Copy failed"))

    def _open_url(self, url: str):
        if "://" not in url:
            url = "https://" + url
        if os.environ.get("APPIMAGE"):
            # Don't let the browser inherit the AppImage's bundled-library environment.
            try:
                subprocess.Popen(["xdg-open", url], env=host_environment(),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return
            except OSError:
                pass
        Gtk.UriLauncher(uri=url).launch(self, None, None, None)

    def _open_current_url(self):
        if self.current_detail and self.current_detail.url:
            self._open_url(self.current_detail.url)

    def _save_attachment(self, e: Entry, att_id: str, fname: str):
        fd = Gtk.FileDialog(initial_name=os.path.basename(fname) or "attachment")

        def chosen(dlg, res):
            try:
                f = dlg.save_finish(res)
            except GLib.Error:
                return
            path = f.get_path()

            def fetch():
                data = self.lp.save_attachment(e.id, att_id)
                fdesc = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fdesc, "wb") as fh:
                    fh.write(data)
            self.worker.submit(fetch, lambda _: self.toast(f"Saved {os.path.basename(path)}"),
                               lambda x: self._error(x, "Could not save attachment"))
        fd.save(self, None, chosen)

    # ---- editing -------------------------------------------------------------------
    def _open_editor(self, is_note: bool, entry: Entry | None = None, detail: Detail | None = None):
        if self.stack.get_visible_child_name() != "vault":
            return

        def on_save(fullname: str, template: str):
            if entry:
                fn, msg, sel = (lambda: self.lp.edit(entry.id, template)), "Saved", entry.id
            else:
                fn, msg, sel = (lambda: self.lp.add(fullname, template)), "Added", None

            def ok(_):
                self.toast(msg)
                self._reload_after_change(sel, fullname)
            self.worker.submit(fn, ok, lambda e: self._error(e, "Could not save item"))
        EditDialog(is_note=is_note, entry=entry, detail=detail, on_save=on_save).present(self)

    def _reload_after_change(self, select_id: Optional[str], fullname: Optional[str] = None):
        def ok(entries):
            self.entries = entries
            sid = select_id
            if sid is None and fullname:
                match = [e for e in entries if e.fullname == fullname]
                sid = match[-1].id if match else None
            self.current = None
            self._populate(sid)
        self.worker.submit(lambda: self.lp.list_entries(sync="no"), ok,
                           lambda e: self._error(e, "Could not refresh vault"))

    def _edit_current(self):
        if self.current and self.current_detail:
            self._open_editor(self.current.is_note, self.current, self.current_detail)

    def _duplicate_current(self):
        e = self.current
        if not e:
            return
        self.worker.submit(lambda: self.lp.duplicate(e.id),
                           lambda _: (self.toast(f"Duplicated {e.name}"),
                                      self._reload_after_change(e.id)),
                           lambda x: self._error(x, "Could not duplicate item"))

    def _confirm_delete(self):
        e = self.current
        if not e:
            return
        dlg = Adw.AlertDialog(heading=f"Delete “{e.name}”?",
                              body="This item will be removed from your LastPass vault.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_close_response("cancel")

        def resp(_d, r):
            if r != "delete":
                return

            def ok(_):
                self.toast(f"Deleted {e.name}")
                self._show_placeholder()
                self._reload_after_change(None)
            self.worker.submit(lambda: self.lp.remove(e.id), ok,
                               lambda x: self._error(x, "Could not delete item"))
        dlg.connect("response", resp)
        dlg.present(self)

    # ---- misc ------------------------------------------------------------------------
    def _about(self):
        about = Adw.AboutDialog(
            application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
            developer_name="lastpass-gtk contributors",
            comments="A native GNOME front end for the LastPass command-line client.\n"
                     "Unofficial; not affiliated with LastPass.",
            license_type=Gtk.License.GPL_2_0,
            website="https://github.com/otrajman/lastpass-gtk",
            issue_url="https://github.com/otrajman/lastpass-gtk/issues",
        )
        about.add_credit_section("Powered by", ["lastpass-cli https://github.com/lastpass/lastpass-cli"])
        about.present(self)

    def _shortcuts(self):
        rows = [
            ("Ctrl+F", "Search"), ("Ctrl+N", "New site"), ("Ctrl+Shift+N", "New secure note"),
            ("Ctrl+E", "Edit item"), ("Ctrl+Shift+C", "Copy password"),
            ("Ctrl+B", "Copy username"), ("Ctrl+U", "Open website"),
            ("Delete", "Delete item"), ("Ctrl+R / F5", "Sync"), ("Ctrl+Q", "Quit"),
        ]
        dlg = Adw.Dialog(title="Keyboard Shortcuts", content_width=380)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        g = Adw.PreferencesGroup()
        for keys, desc in rows:
            r = Adw.ActionRow(title=desc)
            k = Gtk.Label(label=keys)
            k.add_css_class("dim-label")
            r.add_suffix(k)
            g.add(r)
        page = Adw.PreferencesPage()
        page.add(g)
        tv.set_content(page)
        dlg.set_child(tv)
        dlg.present(self)

    def _on_close(self, *_):
        save_state(self.state)
        if self.lp:
            self.lp.close()
        return False


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_prgname(APP_ID)  # matches the .desktop file on X11 and Wayland
        GLib.set_application_name(APP_NAME)
        Gtk.Window.set_default_icon_name(APP_ID)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_: self.quit())
        self.add_action(quit_)

    def do_activate(self):
        win = self.get_active_window() or Window(self)
        win.present()


def main() -> int:
    return Application().run(sys.argv)
