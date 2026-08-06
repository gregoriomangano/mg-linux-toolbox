"""
"Informazioni su M.G Linux Toolbox" — a modal window (not a tab, so it
doesn't clutter the main navigation), reachable from a header-bar button.
Shows version/author, plain-language disclaimer and privacy notes, a
licence placeholder until one is actually chosen, and the update/backup
actions wired to core.updater.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio, GLib, Gdk
import os
import platform
import threading

from core.i18n import T, on_change
from core import version as app_version
from core import release_config
from core.updater import github_provider, update_state, installer, orchestrator
from core.updater.downloader import CancelToken
from core.updater.models import UpdateCheckResult
from ui.license_dialog import show_license_window


def _running_appimage_path() -> str:
    """The AppImage's own path per the AppImage runtime spec, or "" if
    not running as one (e.g. from source during development)."""
    return os.environ.get("APPIMAGE", "")


class AboutWindow(Adw.Window):
    def __init__(self, parent=None):
        super().__init__(modal=True, transient_for=parent)
        self._main_window = parent
        self.set_default_size(480, 640)
        self.set_title(T("about_title"))

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        page = Adw.PreferencesPage()
        scroller.set_child(page)
        toolbar_view.set_content(scroller)

        # ── Identity ──────────────────────────────────────────────
        g_id = Adw.PreferencesGroup()
        page.add(g_id)
        id_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        id_box.set_margin_top(12)
        id_box.set_margin_bottom(12)

        # MG monogram used by the About window.
        logo_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..",
            "assets", "branding", "mg-icon-128.png"
        )
        if os.path.isfile(logo_path):
            logo = Gtk.Image.new_from_file(logo_path)
            logo.set_pixel_size(72)
            logo.set_margin_bottom(6)
            id_box.append(logo)

        name_lbl = Gtk.Label(label=app_version.APP_NAME)
        name_lbl.add_css_class("title-1")
        version_lbl = Gtk.Label(label=app_version.APP_VERSION)
        version_lbl.add_css_class("dim-label")
        author_lbl = Gtk.Label(label=f"{T('about_created_by')}: Gregorio Mangano")
        id_box.append(name_lbl)
        id_box.append(version_lbl)
        id_box.append(author_lbl)
        id_row = Adw.ActionRow()
        id_row.set_activatable(False)
        id_row.set_child(id_box)
        g_id.add(id_row)

        desc_lbl = Gtk.Label(label=T("about_description"), wrap=True, xalign=0)
        desc_lbl.set_margin_top(4)
        desc_row = Adw.ActionRow()
        desc_row.set_activatable(False)
        desc_row.set_child(desc_lbl)
        g_id.add(desc_row)

        # ── Links ─────────────────────────────────────────────────
        g_links = Adw.PreferencesGroup(title=T("about_links_group"))
        page.add(g_links)
        self._add_link_row(g_links, "about_link_website", release_config.WEBSITE_URL)
        self._add_link_row(g_links, "about_link_youtube", release_config.YOUTUBE_URL)
        self._add_link_row(g_links, "about_link_issues", release_config.ISSUES_URL)

        # ── Updates ───────────────────────────────────────────────
        g_update = Adw.PreferencesGroup(title=T("about_updates_group"))
        page.add(g_update)

        self._update_status_lbl = Gtk.Label(wrap=True, xalign=0)
        self._update_status_lbl.set_visible(False)
        update_row = Adw.ActionRow()
        update_row.set_activatable(False)
        update_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._check_update_btn = Gtk.Button(label=T("updater_check_btn"))
        self._check_update_btn.add_css_class("lt-action-btn")
        self._check_update_btn.connect("clicked", self._on_check_updates)
        update_box.append(self._check_update_btn)
        update_box.append(self._update_status_lbl)
        update_row.set_child(update_box)
        g_update.add(update_row)

        # ── Application / managed install ────────────────────────
        g_app = Adw.PreferencesGroup(title=T("about_app_management_group"))
        page.add(g_app)

        running_path = _running_appimage_path()
        is_portable = installer.is_portable_launch(running_path) if running_path else False

        self._add_to_menu_btn = Gtk.Button(label=T("updater_add_to_menu_btn"))
        self._add_to_menu_btn.connect("clicked", self._on_add_to_menu)
        add_menu_row = Adw.ActionRow(subtitle=T("updater_add_to_menu_desc"))
        add_menu_row.add_suffix(self._add_to_menu_btn)
        add_menu_row.set_activatable(False)
        if not running_path:
            self._add_to_menu_btn.set_sensitive(False)
        g_app.add(add_menu_row)

        if not running_path:
            # Running "python3 main.py" straight from a source checkout —
            # there is no AppImage here at all to replace, and this must
            # never be confused with "portable mode" (a real, if ad hoc,
            # AppImage location).
            source_lbl = Gtk.Label(label=T("updater_source_cannot_update"), wrap=True, xalign=0)
            source_lbl.add_css_class("desc-con")
            source_row = Adw.ActionRow()
            source_row.set_activatable(False)
            source_row.set_child(source_lbl)
            g_app.add(source_row)
        elif is_portable:
            portable_lbl = Gtk.Label(label=T("updater_portable_mode"), wrap=True, xalign=0)
            portable_lbl.add_css_class("desc-what")
            portable_row = Adw.ActionRow()
            portable_row.set_activatable(False)
            portable_row.set_child(portable_lbl)
            g_app.add(portable_row)

            if not installer.is_path_writable(running_path):
                readonly_lbl = Gtk.Label(label=T("updater_path_not_writable"), wrap=True, xalign=0)
                readonly_lbl.add_css_class("desc-con")
                readonly_row = Adw.ActionRow()
                readonly_row.set_activatable(False)
                readonly_row.set_child(readonly_lbl)
                g_app.add(readonly_row)

        self._restore_prev_btn = Gtk.Button(label=T("updater_restore_previous_btn"))
        self._restore_prev_btn.connect("clicked", self._on_restore_previous)
        restore_row = Adw.ActionRow()
        restore_row.add_suffix(self._restore_prev_btn)
        restore_row.set_activatable(False)
        g_app.add(restore_row)

        # ── Diagnostics ───────────────────────────────────────────
        g_diag = Adw.PreferencesGroup(title=T("about_diagnostics_group"))
        page.add(g_diag)

        export_btn = Gtk.Button(label=T("about_export_report_btn"))
        export_btn.connect("clicked", self._on_export_report)
        export_row = Adw.ActionRow()
        export_row.add_suffix(export_btn)
        export_row.set_activatable(False)
        g_diag.add(export_row)

        copy_btn = Gtk.Button(label=T("about_copy_version_btn"))
        copy_btn.connect("clicked", self._on_copy_version)
        copy_row = Adw.ActionRow()
        copy_row.add_suffix(copy_btn)
        copy_row.set_activatable(False)
        g_diag.add(copy_row)

        # ── Disclaimer / privacy / license ────────────────────────
        g_legal = Adw.PreferencesGroup(title=T("about_legal_group"))
        page.add(g_legal)
        self._add_text_row(g_legal, T("about_disclaimer"))
        self._add_text_row(g_legal, T("about_disclaimer_best_effort"))
        self._add_text_row(g_legal, T("about_privacy"))
        license_text = release_config.LICENSE_NAME or T("about_license_undecided")
        self._add_text_row(g_legal, f"{T('about_license_label')}: {license_text}")
        license_btn = Gtk.Button(label=T("license_read_btn"))
        license_btn.connect("clicked", lambda _b: show_license_window(self))
        license_row = Adw.ActionRow()
        license_row.add_suffix(license_btn)
        license_row.set_activatable(False)
        g_legal.add(license_row)

        credits_btn = Gtk.Button(label=T("author_credits_btn"))
        credits_btn.connect("clicked", self._on_credits_clicked)
        credits_row = Adw.ActionRow()
        credits_row.add_suffix(credits_btn)
        credits_row.set_activatable(False)
        g_legal.add(credits_row)

    def _add_link_row(self, group, label_key, url):
        if not url:
            return
        row = Adw.ActionRow(title=T(label_key), subtitle=url)
        row.set_activatable(True)
        row.connect("activated", lambda _r: Gio.AppInfo.launch_default_for_uri(url, None))
        icon = Gtk.Image.new_from_icon_name("adw-external-link-symbolic")
        row.add_suffix(icon)
        group.add(row)

    def _add_text_row(self, group, text):
        lbl = Gtk.Label(label=text, wrap=True, xalign=0)
        lbl.add_css_class("sysinfo-value-sub")
        row = Adw.ActionRow()
        row.set_activatable(False)
        row.set_child(lbl)
        group.add(row)

    # ── Update check ──────────────────────────────────────────────
    def _on_check_updates(self, _btn):
        if not release_config.github_configured():
            self._update_status_lbl.set_text(T("updater_not_configured"))
            self._update_status_lbl.set_visible(True)
            return
        self._check_update_btn.set_sensitive(False)
        self._update_status_lbl.set_text(T("updater_checking"))
        self._update_status_lbl.set_visible(True)

        owner, repo = release_config.GITHUB_OWNER, release_config.GITHUB_REPOSITORY
        channel = app_version.UPDATE_CHANNEL
        current = app_version.APP_VERSION

        def run():
            try:
                releases = github_provider.fetch_releases(owner, repo)
                result = update_state.check_for_update(current, releases, channel)
                update_state.record_check_now()
            except github_provider.GithubError as e:
                result = UpdateCheckResult(False, None, current,
                                           friendly_message=e.friendly_message,
                                           technical_detail=e.technical_detail)
            except Exception as e:
                # Never leaves "Controllo in corso…" stuck forever on an
                # error type github_provider doesn't already classify —
                # always a plain sentence, the raw exception only goes
                # into technical_detail (not shown here today, kept for
                # a future "Mostra dettagli").
                result = UpdateCheckResult(False, None, current,
                                           friendly_message="updater_check_failed",
                                           technical_detail=str(e))
            GLib.idle_add(self._on_check_updates_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_check_updates_done(self, result: UpdateCheckResult):
        self._check_update_btn.set_sensitive(True)
        if result.update_available and result.latest is not None:
            self._update_status_lbl.set_text(T("updater_update_available").format(version=result.latest.version))
            self._present_update_dialog(result.latest)
        elif result.friendly_message:
            # Covers both a real error (GithubError, has technical_detail
            # too) and the plain "nothing published for this channel
            # yet" case (check_for_update() with an empty candidate
            # list) — either way, always a simple sentence, never a
            # raw HTTP status or exception text.
            self._update_status_lbl.set_text(T(result.friendly_message))
        else:
            self._update_status_lbl.set_text(T("updater_up_to_date"))
        return False

    # ── One-click update flow (Beta 4) ────────────────────────────
    def _present_update_dialog(self, release):
        """Step 3 of the flow: versions, channel, short notes, size, and
        the real choice — "Aggiorna adesso" / "Non ora"."""
        running_path = _running_appimage_path()
        is_managed = bool(running_path) and installer.is_managed_install(running_path)

        body_lines = [
            f"{T('updater_dialog_installed')}: {app_version.APP_VERSION}",
            f"{T('updater_dialog_available')}: {release.version}",
            T("updater_dialog_channel_stable" if release.channel == "stable"
              else "updater_dialog_channel_beta"),
        ]
        asset = installer.select_asset(release, installer.current_arch())
        if asset is not None and asset.size:
            body_lines.append(f"{T('updater_dialog_size')}: {asset.size / (1024 * 1024):.0f} MB")
        notes = (release.notes or "").strip()
        if notes:
            short = "\n".join(notes.splitlines()[:6])
            body_lines.append("")
            body_lines.append(f"{T('updater_dialog_notes')}:")
            body_lines.append(short)

        dialog = Adw.MessageDialog(transient_for=self, heading=T("updater_dialog_title"),
                                    body="\n".join(body_lines))
        dialog.add_response("later", T("updater_not_now_btn"))
        dialog.add_response("update", T("updater_update_now_btn"))
        dialog.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, r: self._on_update_dialog_response(r, release, is_managed))
        dialog.present()

    def _on_update_dialog_response(self, response, release, is_managed):
        if response != "update":
            return
        if is_managed:
            self._start_managed_update(release)
        else:
            self._present_portable_choice(release)

    def _present_portable_choice(self, release):
        """A running portable AppImage is never overwritten silently —
        the user picks between converting to a managed install or a
        plain verified download to a folder of their choice."""
        dialog = Adw.MessageDialog(transient_for=self,
                                    heading=T("updater_portable_choice_title"),
                                    body=T("updater_portable_choice_body"))
        dialog.add_response("cancel", T("updater_not_now_btn"))
        dialog.add_response("download", T("updater_portable_download_btn"))
        dialog.add_response("install", T("updater_portable_install_btn"))
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, r: self._on_portable_choice(r, release))
        dialog.present()

    def _on_portable_choice(self, response, release):
        if response == "install":
            # Converting to managed: first register the currently
            # running copy as the managed install (menu entry included),
            # then run the exact same managed update on top of it.
            running_path = _running_appimage_path()
            if running_path:
                result = installer.install_to_managed_location(running_path)
                if not result.ok:
                    self._show_update_failure(result)
                    return
            self._start_managed_update(release)
        elif response == "download":
            self._choose_download_folder(release)

    def _choose_download_folder(self, release):
        dialog = Gtk.FileChooserNative.new(
            T("updater_portable_download_btn"), self,
            Gtk.FileChooserAction.SELECT_FOLDER, T("updater_portable_download_btn"), None)

        def on_response(d, response):
            if response == Gtk.ResponseType.ACCEPT:
                folder = d.get_file()
                if folder is not None:
                    self._start_download_only(release, folder.get_path())
            d.destroy()

        dialog.connect("response", on_response)
        dialog.show()

    def _begin_update_ui(self):
        self._cancel_token = CancelToken()
        self._check_update_btn.set_sensitive(False)
        self._update_status_lbl.set_visible(True)

    def _progress_cb(self, downloaded, total):
        percent = int(downloaded * 100 / total) if total else 0
        GLib.idle_add(self._update_status_lbl.set_text,
                      T("updater_downloading").format(percent=percent))

    def _start_managed_update(self, release):
        self._begin_update_ui()

        def run():
            result = orchestrator.perform_managed_update(
                release, app_version.APP_VERSION,
                on_progress=self._progress_cb, cancel_token=self._cancel_token)
            helper_result = None
            if result.ok and orchestrator.helper_update_needed():
                GLib.idle_add(self._update_status_lbl.set_text, T("updater_helper_updating"))
                helper_result = orchestrator.update_helper_from_appimage(
                    installer.MANAGED_APPIMAGE_PATH)
            GLib.idle_add(self._on_managed_update_done, result, helper_result)

        threading.Thread(target=run, daemon=True).start()

    def _on_managed_update_done(self, result, helper_result):
        self._check_update_btn.set_sensitive(True)
        if not result.ok:
            self._show_update_failure(result)
            return False
        message = T("updater_done")
        if helper_result is not None:
            message += "\n" + T("updater_helper_updated" if helper_result.ok
                                 else "updater_helper_update_failed")
        self._update_status_lbl.set_text(message)

        dialog = Adw.MessageDialog(transient_for=self, heading=T("updater_done"), body=message)
        dialog.add_response("later", T("updater_not_now_btn"))
        dialog.add_response("restart", T("updater_restart_now_btn"))
        dialog.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_restart_response)
        dialog.present()
        return False

    def _on_restart_response(self, _dialog, response):
        if response != "restart":
            return
        # Always the stable managed path — never the /tmp/.mount_* path
        # of the currently mounted AppImage.
        if orchestrator.restart_into_managed():
            app = self.get_root().get_application() if self.get_root() else None
            if app is None and self._main_window is not None:
                app = self._main_window.get_application()
            if app is not None:
                app.quit()
        else:
            self._update_status_lbl.set_text(T("updater_replace_failed"))

    def _start_download_only(self, release, dest_dir):
        self._begin_update_ui()

        def run():
            result = orchestrator.download_only(release, dest_dir,
                                                 on_progress=self._progress_cb,
                                                 cancel_token=self._cancel_token)
            GLib.idle_add(self._on_download_only_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_download_only_done(self, result):
        self._check_update_btn.set_sensitive(True)
        if result.ok:
            self._update_status_lbl.set_text(T("updater_download_only_done"))
        else:
            self._show_update_failure(result)
        return False

    def _show_update_failure(self, result):
        """Simple sentence up front; raw detail only behind 'Mostra
        dettagli'."""
        friendly = T(result.friendly_message) if result.friendly_message else T("updater_failed_generic")
        self._update_status_lbl.set_text(friendly)
        dialog = Adw.MessageDialog(transient_for=self, heading=T("updater_failed_generic"),
                                    body=friendly)
        if result.technical_detail:
            expander = Gtk.Expander(label=T("updater_show_details"))
            detail = Gtk.Label(label=result.technical_detail, wrap=True, xalign=0, selectable=True)
            detail.add_css_class("dim-label")
            expander.set_child(detail)
            dialog.set_extra_child(expander)
        dialog.add_response("close", T("updater_not_now_btn"))
        dialog.present()

    # ── Managed install ───────────────────────────────────────────
    def _on_add_to_menu(self, _btn):
        running_path = _running_appimage_path()
        if not running_path:
            return
        self._add_to_menu_btn.set_sensitive(False)

        def run():
            result = installer.install_to_managed_location(running_path)
            GLib.idle_add(self._on_add_to_menu_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_add_to_menu_done(self, result):
        self._add_to_menu_btn.set_sensitive(True)
        if result.ok:
            self._add_to_menu_btn.set_label(T("installed_badge"))
            self._add_to_menu_btn.set_sensitive(False)
        return False

    def _on_restore_previous(self, _btn):
        dialog = Adw.MessageDialog(transient_for=self,
                                    heading=T("updater_restore_confirm_title"),
                                    body=T("updater_restore_confirm_body"))
        dialog.add_response("cancel", T("updater_not_now_btn"))
        dialog.add_response("restore", T("updater_restore_previous_btn"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response",
                       lambda _d, r: self._do_restore_previous() if r == "restore" else None)
        dialog.present()

    def _do_restore_previous(self):
        self._restore_prev_btn.set_sensitive(False)

        def run():
            # There is exactly one kept backup per the spec ("conservare
            # una versione precedente") — its name isn't known ahead of
            # time here since it's versioned; look it up.
            candidate = self._find_backup_file()
            if candidate is None:
                from core.updater.models import InstallResult
                result = InstallResult(False, friendly_message="updater_no_backup_available")
            else:
                result = installer.restore_previous(candidate, installer.MANAGED_APPIMAGE_PATH)
            GLib.idle_add(self._on_restore_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _find_backup_file(self):
        try:
            names = os.listdir(installer.BACKUP_DIR)
        except OSError:
            return None
        candidates = [n for n in names if n.startswith("previous-") and n.endswith(".AppImage")]
        if not candidates:
            return None
        return os.path.join(installer.BACKUP_DIR, sorted(candidates)[-1])

    def _on_restore_done(self, result):
        self._restore_prev_btn.set_sensitive(True)
        self._update_status_lbl.set_visible(True)
        self._update_status_lbl.set_text(
            T("updater_restore_failed") if not result.ok and result.friendly_message != "updater_no_backup_available"
            else T(result.friendly_message) if not result.ok else T("updater_restore_done"))
        return False

    # ── Diagnostics ────────────────────────────────────────────────
    def _diagnostic_report_text(self) -> str:
        lines = [
            f"{app_version.APP_NAME} {app_version.APP_VERSION} ({app_version.UPDATE_CHANNEL})",
            f"Kernel: {platform.release()}",
            f"Python: {platform.python_version()}",
        ]
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        lines.append(f"Distro: {line.split('=', 1)[1].strip().strip(chr(34))}")
                        break
        except OSError:
            pass
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk as _Gtk
            lines.append(f"GTK: {_Gtk.get_major_version()}.{_Gtk.get_minor_version()}.{_Gtk.get_micro_version()}")
        except Exception:
            pass
        return "\n".join(lines) + "\n"

    def _on_export_report(self, _btn):
        dialog = Gtk.FileChooserNative.new(
            T("about_export_report_btn"), self, Gtk.FileChooserAction.SAVE, T("dns_try_btn"), None)
        dialog.set_current_name("mg-linux-toolbox-report.txt")
        dialog.connect("response", self._on_export_report_response)
        dialog.show()

    def _on_export_report_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file is not None:
                try:
                    file.replace_contents(
                        self._diagnostic_report_text().encode(), None, False,
                        Gio.FileCreateFlags.NONE, None)
                except GLib.Error:
                    pass
        dialog.destroy()

    def _on_copy_version(self, _btn):
        clipboard = self.get_clipboard()
        clipboard.set(app_version.display_version())

    def _on_credits_clicked(self, _btn):
        """Navigates to the real in-app "Crediti" page and closes this
        modal — never opens a browser, never a second copy of Crediti."""
        if self._main_window is not None and hasattr(self._main_window, "switch_to_page"):
            self._main_window.switch_to_page("credits")
        self.close()
