"""
"Cronologia e ripristino" — three sections:
1. Activity history — every operation recorded automatically by
   PrivilegedWriter.execute() (core/persistence/history_store.py).
2. Toolbox restore points — named snapshots of every setting the
   Toolbox can read and restore (core/persistence/checkpoint_store.py).
3. Full system snapshots — read-only detection of Timeshift/Snapper/
   Btrfs/transactional-update/rpm-ostree (core/snapshot_tools.py).
"""
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from core.i18n import T, on_change
from core import i18n as _i18n_mod
from core.kernel_features import registry
from core.kernel_features.storage import IOSchedulerFeature
from core.persistence import checkpoint_store as cps
from core.persistence import history_store as hs
from core import snapshot_tools as st
from ui.widgets import make_group
from ui.kernel.friendly_names import friendly_feature_name
from ui.design_system.local_datetime import format_local_datetime

from ui.design_system.page_header import PageHeader, wrap_in_preferences_group

_history_ds_strings = {
    "ds_history_header_desc": {
        "en": "Review applied changes and restore saved values.",
        "it": "Rivedi le modifiche applicate e ripristina i valori salvati.",
        "es": "Revisa los cambios aplicados y restaura los valores guardados.",
        "fr": "Passez en revue les modifications et restaurez les valeurs enregistrées.",
    },
}
for _k, _v in _history_ds_strings.items():
    _i18n_mod._strings[_k] = _v

_PAGE_FILTER_KEYS = [
    ("", "history_filter_all"),
    ("kernel", "tab_kernel"),
    ("performance", "tab_performance"),
    ("audio", "tab_audio"),
    ("virt", "tab_virt"),
    ("security", "tab_security"),
    ("network", "tab_network"),
    ("other", "history_page_other"),
]

_TYPE_FILTER_KEYS = [("", "history_filter_all")] + [
    (t, key) for t, key in (
        (hs.ACTIVATION, "history_type_activation"),
        (hs.DEACTIVATION, "history_type_deactivation"),
        (hs.INSTALLATION, "history_type_installation"),
        (hs.CONFIGURATION, "history_type_configuration"),
        (hs.TEMPORARY_CHANGE, "history_type_temporary_change"),
        (hs.PERMANENT_CHANGE, "history_type_permanent_change"),
        (hs.ERROR, "history_type_error"),
        (hs.VERIFICATION, "history_type_verification"),
        (hs.REBOOT_REQUIRED, "history_type_reboot_required"),
        (hs.RESTORE, "history_type_restore"),
    )
]

_RESULT_FILTER_KEYS = [("", "history_filter_all"), ("ok", "history_result_ok"), ("failed", "history_result_failed")]

_TYPE_LABEL_BY_VALUE = {value: key for value, key in _TYPE_FILTER_KEYS if value}


def _resolve_feature(feature_id: str, device_id):
    """registry.get() only ever holds the LAST-registered instance for a
    given feature_id — harmless for true singletons, but wrong for a
    per-disk feature like the I/O scheduler, so that one is always
    rebuilt fresh from the id it was logged under."""
    if feature_id == "storage.io_scheduler" and device_id:
        return IOSchedulerFeature(device_id=device_id)
    return registry.get(feature_id)


def _detail_label(text: str, css_class: str = "sysinfo-value-sub") -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0, wrap=True)
    lbl.add_css_class(css_class)
    return lbl


class HistoryEntryRow(Adw.ExpanderRow):
    def __init__(self, entry: dict, on_restored):
        super().__init__()
        self._entry = entry
        self._on_restored = on_restored

        title = friendly_feature_name(entry["feature_id"])
        if entry["device_id"]:
            title += f" · {entry['device_id']}"
        self.set_title(title)
        type_key = _TYPE_LABEL_BY_VALUE.get(entry["entry_type"], "history_type_configuration")
        self.set_subtitle(f"{format_local_datetime(entry['timestamp'])}  ·  {T(type_key)}")
        self.add_prefix(Gtk.Image.new_from_icon_name(
            "emblem-ok-symbolic" if entry["result"] == "ok" else "dialog-error-symbolic"))

        if entry.get("restored_at"):
            badge = Gtk.Label(label=T("history_restored_badge"))
            badge.add_css_class("status-active")
            self.add_suffix(badge)
        elif entry.get("reboot_required"):
            badge = Gtk.Label(label=T("history_reboot_pending_badge"))
            badge.add_css_class("badge-reboot")
            self.add_suffix(badge)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body.set_margin_top(8)
        body.set_margin_bottom(10)
        body.set_margin_start(14)
        body.set_margin_end(14)
        body.append(_detail_label(f"{T('history_col_feature_id')}: {entry['feature_id']}"))
        body.append(_detail_label(f"{T('history_col_previous')}: {entry['previous_value']}"))
        body.append(_detail_label(f"{T('history_col_new')}: {entry['new_value']}"))
        body.append(_detail_label(f"{T('history_col_verified')}: {entry['verified_value']}"))
        body.append(_detail_label(f"{T('history_col_distro')}: {entry['distro_id']} ({entry['distro_provider']})"))
        if entry.get("mode"):
            mode_key = "history_mode_temporary" if entry["mode"] == "temporary" else "history_mode_permanent"
            body.append(_detail_label(f"{T('history_col_mode')}: {T(mode_key)}"))
        if entry.get("friendly_message"):
            body.append(_detail_label(T(entry["friendly_message"]),
                                       "desc-con" if entry["result"] != "ok" else "sysinfo-value-sub"))
        self.add_row(body)

        if entry["rollback_available"] and not entry.get("restored_at"):
            restore_btn = Gtk.Button(label=T("history_restore_btn"))
            restore_btn.add_css_class("lt-action-btn")
            restore_btn.set_margin_start(14)
            restore_btn.set_margin_end(14)
            restore_btn.set_margin_bottom(10)
            restore_btn.connect("clicked", self._on_restore_clicked)
            self._restore_btn = restore_btn
            self.add_row(restore_btn)

    def _on_restore_clicked(self, _btn):
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=T("history_restore_confirm_title"),
            body=T("history_restore_confirm_body"),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("history_restore_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_dialog_response)
        dialog.present()

    def _on_dialog_response(self, _dialog, response):
        if response != "confirm":
            return
        self._restore_btn.set_sensitive(False)
        self._restore_btn.set_label("⏳")

        def run():
            feature = _resolve_feature(self._entry["feature_id"], self._entry["device_id"])
            if feature is None:
                GLib.idle_add(self._on_restore_done, False)
                return
            result = feature.restore(force=False)
            if result.ok:
                hs.default_history_store().mark_restored(self._entry["transaction_id"])
            GLib.idle_add(self._on_restore_done, result.ok)

        threading.Thread(target=run, daemon=True).start()

    def _on_restore_done(self, ok: bool):
        self._restore_btn.set_sensitive(True)
        if ok:
            self._restore_btn.set_visible(False)
            badge = Gtk.Label(label=T("history_restored_badge"))
            badge.add_css_class("status-active")
            self.add_suffix(badge)
        else:
            self._restore_btn.set_label(T("history_restore_btn"))
        return False


class ActivityHistorySection:
    def __init__(self, page: Adw.PreferencesPage):
        self.group = make_group("history_grp_activity")
        page.add(self.group)
        self._rows = []
        self._filters = {"search": "", "page": "", "entry_type": "", "result": ""}

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        controls.set_margin_bottom(6)

        self._search = Gtk.SearchEntry(placeholder_text=T("history_search_placeholder"))
        self._search.connect("search-changed", self._on_search_changed)
        controls.append(self._search)

        filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        self._page_dd = self._make_filter_dropdown(_PAGE_FILTER_KEYS, self._on_page_filter_changed)
        self._type_dd = self._make_filter_dropdown(_TYPE_FILTER_KEYS, self._on_type_filter_changed)
        self._result_dd = self._make_filter_dropdown(_RESULT_FILTER_KEYS, self._on_result_filter_changed)
        self._page_dd_label = Gtk.Label(xalign=0)
        self._type_dd_label = Gtk.Label(xalign=0)
        self._result_dd_label = Gtk.Label(xalign=0)
        for lbl in (self._page_dd_label, self._type_dd_label, self._result_dd_label):
            lbl.add_css_class("sysinfo-label")
        filter_row.append(self._labeled_filter(self._page_dd_label, self._page_dd))
        filter_row.append(self._labeled_filter(self._type_dd_label, self._type_dd))
        filter_row.append(self._labeled_filter(self._result_dd_label, self._result_dd))
        controls.append(filter_row)
        self._refresh_filter_labels()
        on_change(self._refresh_filter_labels)

        actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        export_btn = Gtk.Button(label=T("history_export_btn"))
        export_btn.connect("clicked", self._on_export)
        clear_btn = Gtk.Button(label=T("history_clear_btn"))
        clear_btn.add_css_class("destructive-action")
        clear_btn.connect("clicked", self._on_clear)
        actions_row.append(export_btn)
        actions_row.append(clear_btn)
        controls.append(actions_row)

        wrapper = Adw.PreferencesRow(activatable=False, selectable=False)
        wrapper.set_child(controls)
        self.group.add(wrapper)

        self._empty_row = None
        self.refresh()

    def _make_filter_dropdown(self, options, callback) -> Gtk.DropDown:
        model = Gtk.StringList.new([T(key) for _, key in options])
        dd = Gtk.DropDown(model=model)
        dd._options = options  # keep the (value, key) pairs alongside the widget
        dd.connect("notify::selected", callback)
        return dd

    def _labeled_filter(self, label: Gtk.Label, dropdown: Gtk.DropDown) -> Gtk.Widget:
        """A visible caption above each filter — three bare "Tutte"
        dropdowns side by side were ambiguous about what each one even
        filtered. The caption text also becomes the dropdown's tooltip
        and accessible label, so the same real meaning is available to
        keyboard/screen-reader users, not just sighted mouse users."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(label)
        box.append(dropdown)
        return box

    def _refresh_filter_labels(self):
        self._page_dd_label.set_text(T("history_filter_label_page"))
        self._type_dd_label.set_text(T("history_filter_label_type"))
        self._result_dd_label.set_text(T("history_filter_label_result"))
        self._page_dd.set_tooltip_text(T("history_filter_label_page"))
        self._type_dd.set_tooltip_text(T("history_filter_label_type"))
        self._result_dd.set_tooltip_text(T("history_filter_label_result"))

    def _on_search_changed(self, entry):
        self._filters["search"] = entry.get_text().strip()
        self.refresh()

    def _on_page_filter_changed(self, dd, _):
        self._filters["page"] = dd._options[dd.get_selected()][0]
        self.refresh()

    def _on_type_filter_changed(self, dd, _):
        self._filters["entry_type"] = dd._options[dd.get_selected()][0]
        self.refresh()

    def _on_result_filter_changed(self, dd, _):
        self._filters["result"] = dd._options[dd.get_selected()][0]
        self.refresh()

    def refresh(self):
        for row in self._rows:
            self.group.remove(row)
        self._rows = []

        entries = hs.default_history_store().query(
            search=self._filters["search"] or None,
            page=self._filters["page"] or None,
            entry_type=self._filters["entry_type"] or None,
            result=self._filters["result"] or None,
        )
        if not entries:
            empty = Adw.ActionRow(title=T("history_empty"))
            self.group.add(empty)
            self._rows.append(empty)
            return
        for entry in entries:
            row = HistoryEntryRow(entry, self.refresh)
            self.group.add(row)
            self._rows.append(row)

    def _on_export(self, _btn):
        dialog = Gtk.FileChooserNative(
            title=T("history_export_btn"), action=Gtk.FileChooserAction.SAVE,
            transient_for=self.group.get_root(),
        )
        dialog.set_current_name("mg-linux-toolbox-history.json")
        dialog.connect("response", self._on_export_dialog_response)
        dialog.show()
        self._export_dialog = dialog  # keep alive until the response arrives

    def _on_export_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            path = dialog.get_file().get_path()
            try:
                hs.default_history_store().export_json(path)
            except OSError:
                pass
        dialog.destroy()

    def _on_clear(self, _btn):
        dialog = Adw.MessageDialog(
            transient_for=self.group.get_root(),
            heading=T("history_clear_confirm_title"),
            body=T("history_clear_confirm_body"),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("history_clear_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_clear_dialog_response)
        dialog.present()

    def _on_clear_dialog_response(self, _dialog, response):
        if response == "confirm":
            hs.default_history_store().clear()
            self.refresh()


class CheckpointRow(Adw.ActionRow):
    def __init__(self, summary: dict, on_changed):
        super().__init__()
        self._summary = summary
        self._on_changed = on_changed
        self.set_title(summary["name"])
        self.set_subtitle(f"{T('checkpoint_created_at_label')}: {format_local_datetime(summary['created_at'])}  ·  "
                           f"{summary['entry_count']} {T('checkpoint_entries_label')}")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        view_btn = Gtk.Button(label=T("checkpoint_view_btn"))
        view_btn.connect("clicked", self._on_view)
        restore_btn = Gtk.Button(label=T("checkpoint_restore_btn"))
        restore_btn.add_css_class("lt-action-btn")
        restore_btn.connect("clicked", self._on_restore)
        export_btn = Gtk.Button(label=T("checkpoint_export_btn"))
        export_btn.connect("clicked", self._on_export)
        delete_btn = Gtk.Button(label=T("checkpoint_delete_btn"))
        delete_btn.add_css_class("destructive-action")
        delete_btn.connect("clicked", self._on_delete)
        for b in (view_btn, restore_btn, export_btn, delete_btn):
            box.append(b)
        self.add_suffix(box)
        self._restore_btn = restore_btn

    def _on_view(self, _btn):
        checkpoint = cps.get(self._summary["id"])
        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if not checkpoint or not checkpoint.entries:
            body_box.append(_detail_label(T("checkpoint_empty")))
        for entry in (checkpoint.entries if checkpoint else []):
            label = friendly_feature_name(entry.feature_id) + (f" · {entry.device_id}" if entry.device_id else "")
            body_box.append(_detail_label(f"{label}: {entry.value}"))
        scroller = Gtk.ScrolledWindow(min_content_height=200, max_content_height=400)
        scroller.set_child(body_box)
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(), heading=T("checkpoint_view_dialog_title"),
        )
        dialog.set_extra_child(scroller)
        dialog.add_response("close", T("dialog_close_btn"))
        dialog.present()

    def _on_restore(self, _btn):
        plan = cps.plan_restore(self._summary["id"])
        if not plan:
            dialog = Adw.MessageDialog(
                transient_for=self.get_root(), heading=T("checkpoint_restore_btn"),
                body=T("checkpoint_restore_nothing_to_change"),
            )
            dialog.add_response("close", T("dialog_close_btn"))
            dialog.present()
            return

        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for step in plan:
            label = friendly_feature_name(step["feature_id"]) + (f" · {step['device_id']}" if step["device_id"] else "")
            body_box.append(_detail_label(f"{label}: {step['current']} → {step['target']}"))
        scroller = Gtk.ScrolledWindow(min_content_height=150, max_content_height=350)
        scroller.set_child(body_box)

        dialog = Adw.MessageDialog(
            transient_for=self.get_root(), heading=T("checkpoint_restore_confirm_title"),
        )
        dialog.set_extra_child(scroller)
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("checkpoint_restore_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_restore_confirm)
        dialog.present()

    def _on_restore_confirm(self, _dialog, response):
        if response != "confirm":
            return
        self._restore_btn.set_sensitive(False)
        self._restore_btn.set_label("⏳")

        def run():
            report = cps.restore(self._summary["id"])
            GLib.idle_add(self._on_restore_done, report)

        threading.Thread(target=run, daemon=True).start()

    def _on_restore_done(self, report):
        self._restore_btn.set_sensitive(True)
        self._restore_btn.set_label(T("checkpoint_restore_btn"))
        status_key = {
            "success": "checkpoint_restore_success",
            "partial": "checkpoint_restore_partial",
            "failed": "checkpoint_restore_failed",
        }[report.status]
        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body_box.append(_detail_label(T(status_key)))
        for step in report.steps:
            label = friendly_feature_name(step.feature_id) + (f" · {step.device_id}" if step.device_id else "")
            body_box.append(_detail_label(f"{label}: {step.outcome}" + (f" — {step.detail}" if step.detail else "")))
        dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=T(status_key))
        scroller = Gtk.ScrolledWindow(min_content_height=100, max_content_height=300)
        scroller.set_child(body_box)
        dialog.set_extra_child(scroller)
        dialog.add_response("close", T("dialog_close_btn"))
        dialog.present()
        self._on_changed()
        return False

    def _on_export(self, _btn):
        dialog = Gtk.FileChooserNative(
            title=T("checkpoint_export_btn"), action=Gtk.FileChooserAction.SAVE,
            transient_for=self.get_root(),
        )
        dialog.set_current_name(f"{self._summary['name']}.json")
        dialog.connect("response", self._on_export_dialog_response)
        dialog.show()
        self._export_dialog = dialog

    def _on_export_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            cps.export_checkpoint(self._summary["id"], dialog.get_file().get_path())
        dialog.destroy()

    def _on_delete(self, _btn):
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(), heading=T("checkpoint_delete_confirm_title"),
            body=T("checkpoint_delete_confirm_body"),
        )
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("checkpoint_delete_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_confirm)
        dialog.present()

    def _on_delete_confirm(self, _dialog, response):
        if response == "confirm":
            cps.delete(self._summary["id"])
            self._on_changed()


class CheckpointsSection:
    def __init__(self, page: Adw.PreferencesPage):
        self.group = make_group("history_grp_checkpoints")
        page.add(self.group)
        self._rows = []

        create_btn = Gtk.Button(label=T("checkpoint_create_btn"))
        create_btn.add_css_class("lt-action-btn")
        create_btn.connect("clicked", self._on_create)
        wrapper = Adw.PreferencesRow(activatable=False, selectable=False)
        wrapper.set_child(create_btn)
        self.group.add(wrapper)
        self._create_btn = create_btn

        self.refresh()

    def _on_create(self, _btn):
        entry = Gtk.Entry(placeholder_text=T("checkpoint_name_placeholder"))
        dialog = Adw.MessageDialog(
            transient_for=self.group.get_root(), heading=T("checkpoint_create_dialog_title"),
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", T("kf_dialog_cancel"))
        dialog.add_response("confirm", T("checkpoint_create_btn"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: self._on_create_confirm(r, entry.get_text()))
        dialog.present()
        entry.grab_focus()

    def _on_create_confirm(self, response, name):
        if response != "confirm":
            return
        name = name.strip() or T("checkpoint_default_name")
        self._create_btn.set_sensitive(False)

        def run():
            cps.create(name)
            GLib.idle_add(self._on_created)

        threading.Thread(target=run, daemon=True).start()

    def _on_created(self):
        self._create_btn.set_sensitive(True)
        self.refresh()
        return False

    def refresh(self):
        for row in self._rows:
            self.group.remove(row)
        self._rows = []
        summaries = cps.list_checkpoints()
        if not summaries:
            empty = Adw.ActionRow(title=T("checkpoint_empty"))
            self.group.add(empty)
            self._rows.append(empty)
            return
        for summary in summaries:
            row = CheckpointRow(summary, self.refresh)
            self.group.add(row)
            self._rows.append(row)


_SNAPSHOT_NAME_KEYS = {
    st.TIMESHIFT: "snapshot_tool_timeshift",
    st.SNAPPER: "snapshot_tool_snapper",
    st.BTRFS: "snapshot_tool_btrfs",
    st.TRANSACTIONAL_UPDATE: "snapshot_tool_transactional_update",
    st.RPM_OSTREE: "snapshot_tool_rpm_ostree",
}

_SNAPSHOT_LISTERS = {
    st.TIMESHIFT: st.list_timeshift_snapshots,
    st.SNAPPER: st.list_snapper_snapshots,
}


class SnapshotToolRow(Adw.ActionRow):
    def __init__(self, status: dict):
        super().__init__()
        self.set_title(T(_SNAPSHOT_NAME_KEYS[status["tool"]]))
        if status["configured"]:
            status_text, css = T("snapshot_status_ready"), "status-active"
        elif status["installed"]:
            status_text, css = T("snapshot_status_installed_only"), "sysinfo-value"
        else:
            status_text, css = T("snapshot_status_not_installed"), "sysinfo-value-sub"
        self.set_subtitle(status_text)

        lister = _SNAPSHOT_LISTERS.get(status["tool"])
        if status["configured"] and lister is not None:
            btn = Gtk.Button(label=T("snapshot_list_btn"), valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda _b: self._on_view(lister))
            self.add_suffix(btn)

    def _on_view(self, lister):
        snapshots = lister()
        body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if not snapshots:
            body_box.append(_detail_label(T("snapshot_none_found")))
        for s in snapshots:
            body_box.append(_detail_label(str(s)))
        scroller = Gtk.ScrolledWindow(min_content_height=100, max_content_height=300)
        scroller.set_child(body_box)
        dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=T("snapshot_list_dialog_title"))
        dialog.set_extra_child(scroller)
        dialog.add_response("close", T("dialog_close_btn"))
        dialog.present()


class SnapshotToolsSection:
    def __init__(self, page: Adw.PreferencesPage):
        self.group = make_group("history_grp_snapshots")
        page.add(self.group)
        self._rows = []
        self.refresh()

    def refresh(self):
        for row in self._rows:
            self.group.remove(row)
        self._rows = []
        for status in st.detect_tools():
            row = SnapshotToolRow(status)
            self.group.add(row)
            self._rows.append(row)


class HistoryPage(Adw.PreferencesPage):
    def __init__(self):
        super().__init__()
        self.set_icon_name("document-open-recent-symbolic")
        on_change(self._refresh_title)
        self._refresh_title()

        header = PageHeader(
            "document-open-recent-symbolic", T("tab_history"), T("ds_history_header_desc"),
            category="neutral",
        )
        self.add(wrap_in_preferences_group(header))

        self._activity = ActivityHistorySection(self)
        self._checkpoints = CheckpointsSection(self)
        self._snapshots = SnapshotToolsSection(self)

    def _refresh_title(self):
        self.set_title(T("tab_history"))
