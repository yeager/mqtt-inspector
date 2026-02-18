#!/usr/bin/env python3

import sys
import os
import json
import csv
import io
import uuid
import time
import gettext
import threading
from collections import defaultdict, deque
from datetime import datetime

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango

import paho.mqtt.client as mqtt

from mqtt_inspector import __version__

# Set up gettext
TEXTDOMAIN = 'mqtt-inspector'
gettext.textdomain(TEXTDOMAIN)
gettext.bindtextdomain(TEXTDOMAIN, '/usr/share/locale')
_ = gettext.gettext

PROFILES_DIR = os.path.expanduser("~/.config/mqtt-inspector")
PROFILES_FILE = os.path.join(PROFILES_DIR, "profiles.json")
MAX_HISTORY = 100


def _load_profiles():
    """Load connection profiles from disk."""
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_profiles(profiles):
    """Save connection profiles to disk."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    with open(PROFILES_FILE, "w") as f:
        json.dump(profiles, f, indent=2)


def _is_json(payload):
    """Check if a payload string is valid JSON."""
    try:
        json.loads(payload)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def _pretty_json(payload):
    """Pretty-print a JSON payload."""
    try:
        return json.dumps(json.loads(payload), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        return payload


def _to_hex(data):
    """Convert bytes to hex view."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:08x}  {hex_part:<48s}  {ascii_part}")
    return "\n".join(lines)


def _timestamp():
    """Return current timestamp string."""
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Connection dialog
# ---------------------------------------------------------------------------

class ConnectionDialog(Adw.Dialog):
    """Dialog for configuring broker connection."""

    def __init__(self, parent_window, on_connect_cb, **kwargs):
        super().__init__(**kwargs)
        self._parent_window = parent_window
        self._on_connect_cb = on_connect_cb

        self.set_title(_("Connect to Broker"))
        self.set_content_width(440)
        self.set_content_height(520)

        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()
        toolbar_view.set_content(page)

        # --- Profile selector ---
        profile_group = Adw.PreferencesGroup(title=_("Profile"))
        page.add(profile_group)

        self._profiles = _load_profiles()
        self._profile_row = Adw.ComboRow(title=_("Saved Profiles"))
        profile_model = Gtk.StringList()
        profile_model.append(_("(New)"))
        for p in self._profiles:
            profile_model.append(p.get("name", p.get("host", "?")))
        self._profile_row.set_model(profile_model)
        self._profile_row.connect("notify::selected", self._on_profile_selected)
        profile_group.add(self._profile_row)

        # --- Connection settings ---
        conn_group = Adw.PreferencesGroup(title=_("Connection"))
        page.add(conn_group)

        self._host_row = Adw.EntryRow(title=_("Host"))
        self._host_row.set_text("localhost")
        conn_group.add(self._host_row)

        self._port_row = Adw.SpinRow.new_with_range(1, 65535, 1)
        self._port_row.set_title(_("Port"))
        self._port_row.set_value(1883)
        conn_group.add(self._port_row)

        self._tls_row = Adw.SwitchRow(title=_("Use TLS"))
        self._tls_row.connect("notify::active", self._on_tls_toggled)
        conn_group.add(self._tls_row)

        self._client_id_row = Adw.EntryRow(title=_("Client ID"))
        self._client_id_row.set_text("")
        conn_group.add(self._client_id_row)

        # --- Auth ---
        auth_group = Adw.PreferencesGroup(title=_("Authentication"))
        page.add(auth_group)

        self._user_row = Adw.EntryRow(title=_("Username"))
        auth_group.add(self._user_row)

        self._pass_row = Adw.PasswordEntryRow(title=_("Password"))
        auth_group.add(self._pass_row)

        # --- Buttons ---
        btn_group = Adw.PreferencesGroup()
        page.add(btn_group)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.END)

        save_btn = Gtk.Button(label=_("Save Profile"))
        save_btn.connect("clicked", self._on_save_profile)
        btn_box.append(save_btn)

        delete_btn = Gtk.Button(label=_("Delete Profile"))
        delete_btn.add_css_class("destructive-action")
        delete_btn.connect("clicked", self._on_delete_profile)
        btn_box.append(delete_btn)

        connect_btn = Gtk.Button(label=_("Connect"))
        connect_btn.add_css_class("suggested-action")
        connect_btn.connect("clicked", self._on_connect)
        btn_box.append(connect_btn)

        btn_group.add(btn_box)

    # helpers ---------------------------------------------------------------

    def _get_params(self):
        client_id = self._client_id_row.get_text().strip()
        if not client_id:
            client_id = f"mqtt-inspector-{uuid.uuid4().hex[:8]}"
        return {
            "host": self._host_row.get_text().strip() or "localhost",
            "port": int(self._port_row.get_value()),
            "tls": self._tls_row.get_active(),
            "client_id": client_id,
            "username": self._user_row.get_text().strip(),
            "password": self._pass_row.get_text().strip(),
        }

    def _apply_profile(self, profile):
        self._host_row.set_text(profile.get("host", "localhost"))
        self._port_row.set_value(profile.get("port", 1883))
        self._tls_row.set_active(profile.get("tls", False))
        self._client_id_row.set_text(profile.get("client_id", ""))
        self._user_row.set_text(profile.get("username", ""))
        self._pass_row.set_text(profile.get("password", ""))

    # signal handlers -------------------------------------------------------

    def _on_tls_toggled(self, row, _pspec):
        if row.get_active() and int(self._port_row.get_value()) == 1883:
            self._port_row.set_value(8883)
        elif not row.get_active() and int(self._port_row.get_value()) == 8883:
            self._port_row.set_value(1883)

    def _on_profile_selected(self, row, _pspec):
        idx = row.get_selected()
        if idx > 0 and idx - 1 < len(self._profiles):
            self._apply_profile(self._profiles[idx - 1])

    def _on_save_profile(self, _btn):
        params = self._get_params()
        params["name"] = params["host"]
        # Update existing or append
        found = False
        for i, p in enumerate(self._profiles):
            if p.get("host") == params["host"] and p.get("port") == params["port"]:
                self._profiles[i] = params
                found = True
                break
        if not found:
            self._profiles.append(params)
        _save_profiles(self._profiles)

    def _on_delete_profile(self, _btn):
        idx = self._profile_row.get_selected()
        if idx > 0 and idx - 1 < len(self._profiles):
            del self._profiles[idx - 1]
            _save_profiles(self._profiles)

    def _on_connect(self, _btn):
        params = self._get_params()
        self.close()
        if self._on_connect_cb:
            self._on_connect_cb(params)


# ---------------------------------------------------------------------------
# Export dialog
# ---------------------------------------------------------------------------

class ExportDialog(Adw.Dialog):
    """Export current messages as CSV or JSON."""

    def __init__(self, messages, **kwargs):
        super().__init__(**kwargs)
        self._messages = messages

        self.set_title(_("Export Messages"))
        self.set_content_width(340)
        self.set_content_height(220)

        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()
        toolbar_view.set_content(page)

        group = Adw.PreferencesGroup(title=_("Format"))
        page.add(group)

        self._format_row = Adw.ComboRow(title=_("File Format"))
        fmt_model = Gtk.StringList()
        fmt_model.append("CSV")
        fmt_model.append("JSON")
        self._format_row.set_model(fmt_model)
        group.add(self._format_row)

        btn_group = Adw.PreferencesGroup()
        page.add(btn_group)

        export_btn = Gtk.Button(label=_("Export"))
        export_btn.add_css_class("suggested-action")
        export_btn.set_halign(Gtk.Align.END)
        export_btn.connect("clicked", self._on_export)
        btn_group.add(export_btn)

    def _on_export(self, _btn):
        fmt = "csv" if self._format_row.get_selected() == 0 else "json"
        data = self._generate(fmt)

        dialog = Gtk.FileDialog()
        dialog.set_initial_name(f"mqtt-export.{fmt}")
        dialog.save(
            None,
            None,
            self._on_file_saved,
            data,
        )

    def _on_file_saved(self, dialog, result, data):
        try:
            gfile = dialog.save_finish(result)
            path = gfile.get_path()
            with open(path, "w") as f:
                f.write(data)
        except GLib.Error:
            pass
        self.close()

    def _generate(self, fmt):
        rows = []
        for msg in self._messages:
            rows.append({
                "topic": msg.get("topic", ""),
                "payload": msg.get("payload", ""),
                "qos": msg.get("qos", 0),
                "retain": msg.get("retain", False),
                "timestamp": msg.get("timestamp", ""),
            })
        if fmt == "json":
            return json.dumps(rows, indent=2, ensure_ascii=False)
        else:
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=["topic", "payload", "qos", "retain", "timestamp"])
            writer.writeheader()
            writer.writerows(rows)
            return out.getvalue()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.set_default_size(1100, 700)
        self.set_title(_("MQTT Inspector"))

        # MQTT state
        self._client = None
        self._connected = False
        self._broker_name = ""
        self._messages = defaultdict(lambda: deque(maxlen=MAX_HISTORY))
        self._topic_counts = defaultdict(int)
        self._selected_topic = None
        self._hex_view = False
        self._subscription = "#"

        # --- Build UI ---
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar
        header = Adw.HeaderBar()
        self.set_titlebar(header)

        # Connection button + indicator
        conn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._conn_indicator = Gtk.DrawingArea()
        self._conn_indicator.set_content_width(12)
        self._conn_indicator.set_content_height(12)
        self._conn_indicator.set_draw_func(self._draw_indicator)
        conn_box.append(self._conn_indicator)

        self._conn_button = Gtk.Button(label=_("Connect"))
        self._conn_button.connect("clicked", self._on_connect_clicked)
        conn_box.append(self._conn_button)
        header.pack_start(conn_box)

        self._broker_label = Gtk.Label(label="")
        self._broker_label.add_css_class("dim-label")
        header.pack_start(self._broker_label)

        # Subscribe filter
        self._subscribe_entry = Gtk.Entry()
        self._subscribe_entry.set_placeholder_text(_("Subscribe filter (e.g. home/#)"))
        self._subscribe_entry.set_width_chars(24)
        self._subscribe_entry.connect("activate", self._on_subscribe_activate)
        header.pack_start(self._subscribe_entry)

        # Publish button in header
        publish_btn = Gtk.Button(icon_name="mail-send-symbolic")
        publish_btn.set_tooltip_text(_("Show Publish Panel"))
        publish_btn.connect("clicked", self._toggle_publish_panel)
        header.pack_end(publish_btn)

        # Paned: left tree | right detail
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._paned.set_position(320)
        self._paned.set_vexpand(True)
        main_box.append(self._paned)

        # --- Left pane: topic tree ---
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left_scroll = Gtk.ScrolledWindow()
        left_scroll.set_vexpand(True)

        # TreeStore: [topic_segment, full_topic, count_str]
        self._tree_store = Gtk.TreeStore.new([str, str, str])
        self._tree_view = Gtk.TreeView(model=self._tree_store)
        self._tree_view.set_headers_visible(False)
        self._tree_view.connect("cursor-changed", self._on_topic_selected)

        col = Gtk.TreeViewColumn()
        cell_text = Gtk.CellRendererText()
        cell_count = Gtk.CellRendererText()
        cell_count.set_property("foreground", "#888888")
        col.pack_start(cell_text, True)
        col.pack_start(cell_count, False)
        col.add_attribute(cell_text, "text", 0)
        col.add_attribute(cell_count, "text", 2)
        self._tree_view.append_column(col)

        left_scroll.set_child(self._tree_view)
        left_box.append(left_scroll)
        self._paned.set_start_child(left_box)

        # --- Right pane: message detail ---
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        right_box.set_margin_start(8)
        right_box.set_margin_end(8)
        right_box.set_margin_top(4)

        # Metadata bar
        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._meta_label = Gtk.Label(label="")
        self._meta_label.set_halign(Gtk.Align.START)
        self._meta_label.add_css_class("dim-label")
        meta_box.append(self._meta_label)

        self._hex_toggle = Gtk.ToggleButton(label=_("Hex"))
        self._hex_toggle.connect("toggled", self._on_hex_toggled)
        meta_box.append(self._hex_toggle)

        clear_btn = Gtk.Button(label=_("Clear History"))
        clear_btn.connect("clicked", self._on_clear_history)
        meta_box.append(clear_btn)

        right_box.append(meta_box)

        # Message history list
        self._history_scroll = Gtk.ScrolledWindow()
        self._history_scroll.set_vexpand(True)
        self._history_list = Gtk.ListBox()
        self._history_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._history_list.connect("row-selected", self._on_history_row_selected)
        self._history_scroll.set_child(self._history_list)
        right_box.append(self._history_scroll)

        # Payload view
        payload_scroll = Gtk.ScrolledWindow()
        payload_scroll.set_vexpand(True)
        self._payload_view = Gtk.TextView()
        self._payload_view.set_editable(False)
        self._payload_view.set_monospace(True)
        self._payload_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        payload_scroll.set_child(self._payload_view)
        right_box.append(payload_scroll)

        self._paned.set_end_child(right_box)

        # --- Publish panel (hidden by default) ---
        self._publish_revealer = Gtk.Revealer()
        self._publish_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        pub_frame = Gtk.Frame()
        pub_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pub_box.set_margin_start(8)
        pub_box.set_margin_end(8)
        pub_box.set_margin_top(4)
        pub_box.set_margin_bottom(4)

        pub_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pub_label = Gtk.Label(label=_("Publish"))
        pub_label.add_css_class("heading")
        pub_top.append(pub_label)

        self._pub_topic_entry = Gtk.Entry()
        self._pub_topic_entry.set_placeholder_text(_("Topic"))
        self._pub_topic_entry.set_hexpand(True)
        pub_top.append(self._pub_topic_entry)

        # QoS selector
        self._pub_qos = Gtk.DropDown.new_from_strings(["QoS 0", "QoS 1", "QoS 2"])
        self._pub_qos.set_selected(0)
        pub_top.append(self._pub_qos)

        self._pub_retain = Gtk.CheckButton(label=_("Retain"))
        pub_top.append(self._pub_retain)

        pub_send_btn = Gtk.Button(label=_("Send"))
        pub_send_btn.add_css_class("suggested-action")
        pub_send_btn.connect("clicked", self._on_publish)
        pub_top.append(pub_send_btn)

        pub_box.append(pub_top)

        pub_payload_scroll = Gtk.ScrolledWindow()
        pub_payload_scroll.set_min_content_height(60)
        self._pub_payload = Gtk.TextView()
        self._pub_payload.set_monospace(True)
        self._pub_payload.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        pub_payload_scroll.set_child(self._pub_payload)
        pub_box.append(pub_payload_scroll)

        pub_frame.set_child(pub_box)
        self._publish_revealer.set_child(pub_frame)
        main_box.append(self._publish_revealer)

        # Ctrl+Enter to publish
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        # --- Status bar ---
        self._status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._status_bar.set_margin_start(8)
        self._status_bar.set_margin_end(8)
        self._status_bar.set_margin_top(2)
        self._status_bar.set_margin_bottom(2)

        self._status_label = Gtk.Label(label=_("Disconnected"))
        self._status_label.set_halign(Gtk.Align.START)
        self._status_label.set_hexpand(True)
        self._status_label.add_css_class("dim-label")
        self._status_bar.append(self._status_label)

        self._msg_count_label = Gtk.Label(label="")
        self._msg_count_label.add_css_class("dim-label")
        self._status_bar.append(self._msg_count_label)

        main_box.append(self._status_bar)

        # Track iters for tree
        self._tree_iters = {}  # full_topic -> iter
        self._total_messages = 0

    # --- Drawing -----------------------------------------------------------

    def _draw_indicator(self, area, cr, width, height):
        if self._connected:
            cr.set_source_rgb(0.2, 0.8, 0.2)
        else:
            cr.set_source_rgb(0.8, 0.2, 0.2)
        cr.arc(width / 2, height / 2, min(width, height) / 2 - 1, 0, 2 * 3.14159)
        cr.fill()

    # --- Connection --------------------------------------------------------

    def _on_connect_clicked(self, _btn):
        if self._connected:
            self._disconnect()
        else:
            dialog = ConnectionDialog(self, self._do_connect)
            dialog.present(self)

    def _do_connect(self, params):
        self._set_status(_("Connecting to %s:%d…") % (params["host"], params["port"]))
        self._broker_name = params["host"]
        self._broker_label.set_text(params["host"])

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=params["client_id"],
        )
        if params["username"]:
            self._client.username_pw_set(params["username"], params["password"] or None)
        if params["tls"]:
            self._client.tls_set()

        self._client.on_connect = self._on_mqtt_connect
        self._client.on_disconnect = self._on_mqtt_disconnect
        self._client.on_message = self._on_mqtt_message

        try:
            self._client.connect_async(params["host"], params["port"], keepalive=60)
            self._client.loop_start()
        except Exception as e:
            self._set_status(_("Connection failed: %s") % str(e))

    def _disconnect(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False
        self._conn_button.set_label(_("Connect"))
        self._broker_label.set_text("")
        self._conn_indicator.queue_draw()
        self._set_status(_("Disconnected"))

    # --- MQTT callbacks (called from MQTT thread) --------------------------

    def _on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0 or (hasattr(rc, 'value') and rc.value == 0):
            sub = self._subscription or "#"
            client.subscribe(sub)
            GLib.idle_add(self._ui_connected)
        else:
            GLib.idle_add(self._set_status, _("Connection refused (rc=%s)") % str(rc))

    def _on_mqtt_disconnect(self, client, userdata, flags, rc, properties=None):
        GLib.idle_add(self._ui_disconnected)

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            payload_str = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload_str = msg.payload.hex()
        entry = {
            "topic": msg.topic,
            "payload": payload_str,
            "payload_bytes": bytes(msg.payload),
            "qos": msg.qos,
            "retain": bool(msg.retain),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        GLib.idle_add(self._ui_message, entry)

    # --- UI updates from MQTT thread via idle_add --------------------------

    def _ui_connected(self):
        self._connected = True
        self._conn_button.set_label(_("Disconnect"))
        self._conn_indicator.queue_draw()
        self._set_status(_("Connected to %s") % self._broker_name)

    def _ui_disconnected(self):
        self._connected = False
        self._conn_button.set_label(_("Connect"))
        self._conn_indicator.queue_draw()
        self._set_status(_("Disconnected"))

    def _ui_message(self, entry):
        topic = entry["topic"]
        self._messages[topic].append(entry)
        self._topic_counts[topic] += 1
        self._total_messages += 1

        # Update tree
        self._ensure_topic_in_tree(topic)

        # Update count display
        self._msg_count_label.set_text(
            _("Messages: %d") % self._total_messages
        )

        # If this topic is selected, refresh detail
        if self._selected_topic == topic:
            self._refresh_detail()

        self._set_status(
            _("Last: %s on %s") % (_timestamp(), topic)
        )

    def _ensure_topic_in_tree(self, topic):
        """Add topic segments to tree store if not present, update count."""
        parts = topic.split("/")
        parent_iter = None
        path_so_far = ""
        for part in parts:
            path_so_far = f"{path_so_far}/{part}" if path_so_far else part
            if path_so_far not in self._tree_iters:
                it = self._tree_store.append(parent_iter, [
                    part,
                    path_so_far,
                    "",
                ])
                self._tree_iters[path_so_far] = it
            parent_iter = self._tree_iters[path_so_far]
        # Update count on the leaf
        count = self._topic_counts[topic]
        self._tree_store.set_value(
            self._tree_iters[topic], 2, f"  ({count})"
        )

    # --- Topic selection ---------------------------------------------------

    def _on_topic_selected(self, tree_view):
        selection = tree_view.get_selection()
        model, it = selection.get_selected()
        if it:
            self._selected_topic = model.get_value(it, 1)
            self._refresh_detail()

    def _refresh_detail(self):
        topic = self._selected_topic
        if not topic:
            return

        msgs = list(self._messages.get(topic, []))

        # Update history list
        self._history_list.remove_all()
        for msg in msgs:
            label = Gtk.Label(
                label=f"[{msg['timestamp']}] QoS {msg['qos']}"
                      + (" R" if msg["retain"] else "")
                      + f" — {msg['payload'][:60]}"
            )
            label.set_halign(Gtk.Align.START)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            row = Gtk.ListBoxRow()
            row.set_child(label)
            row.msg = msg
            self._history_list.append(row)

        # Show latest message payload
        if msgs:
            self._show_payload(msgs[-1])

    def _on_history_row_selected(self, listbox, row):
        if row and hasattr(row, 'msg'):
            self._show_payload(row.msg)

    def _show_payload(self, msg):
        payload = msg["payload"]
        meta = _("Topic: %s | QoS: %d | Retain: %s | Time: %s") % (
            msg["topic"],
            msg["qos"],
            _("Yes") if msg["retain"] else _("No"),
            msg["timestamp"],
        )
        self._meta_label.set_text(meta)

        buf = self._payload_view.get_buffer()
        if self._hex_view:
            buf.set_text(_to_hex(msg.get("payload_bytes", payload.encode())))
        elif _is_json(payload):
            buf.set_text(_pretty_json(payload))
        else:
            buf.set_text(payload)

    # --- Hex toggle --------------------------------------------------------

    def _on_hex_toggled(self, btn):
        self._hex_view = btn.get_active()
        self._refresh_detail()

    # --- Clear history -----------------------------------------------------

    def _on_clear_history(self, _btn):
        if self._selected_topic:
            self._messages[self._selected_topic].clear()
            self._refresh_detail()

    # --- Subscribe filter --------------------------------------------------

    def _on_subscribe_activate(self, entry):
        new_filter = entry.get_text().strip() or "#"
        if self._client and self._connected:
            self._client.unsubscribe(self._subscription)
            self._subscription = new_filter
            self._client.subscribe(self._subscription)
            self._set_status(_("Subscribed to %s") % self._subscription)

    # --- Publish -----------------------------------------------------------

    def _toggle_publish_panel(self, _btn):
        revealed = self._publish_revealer.get_child_revealed()
        self._publish_revealer.set_reveal_child(not revealed)

    def _on_publish(self, _btn=None):
        if not self._client or not self._connected:
            self._set_status(_("Not connected"))
            return
        topic = self._pub_topic_entry.get_text().strip()
        if not topic:
            return
        buf = self._pub_payload.get_buffer()
        payload = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        qos = self._pub_qos.get_selected()
        retain = self._pub_retain.get_active()
        self._client.publish(topic, payload, qos=qos, retain=retain)
        self._set_status(
            _("Published to %s at %s") % (topic, _timestamp())
        )

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if (keyval == Gdk.KEY_Return and
                state & Gdk.ModifierType.CONTROL_MASK):
            self._on_publish()
            return True
        return False

    # --- Status bar --------------------------------------------------------

    def _set_status(self, text):
        self._status_label.set_text(f"[{_timestamp()}] {text}")

    # --- About / Shortcuts -------------------------------------------------

    def show_about(self, action, param):
        about = Adw.AboutDialog()
        about.set_application_name(_("MQTT Inspector"))
        about.set_application_icon("se.danielnylander.mqtt-inspector")
        about.set_developer_name("Daniel Nylander")
        about.set_developers(["Daniel Nylander <daniel@danielnylander.se>"])
        about.set_version(__version__)
        about.set_website("https://github.com/yeager/mqtt-inspector")
        about.set_license_type(Gtk.License.GPL_3_0)
        about.set_issue_url("https://github.com/yeager/mqtt-inspector/issues")
        about.set_translator_credits(_("Translate this app: https://www.transifex.com/danielnylander/mqtt-inspector/"))
        about.present(self)

    def show_shortcuts(self, action, param):
        builder = Gtk.Builder()
        builder.add_from_string('''
        <interface>
          <object class="GtkShortcutsWindow" id="shortcuts">
            <property name="modal">True</property>
            <child>
              <object class="GtkShortcutsSection">
                <property name="section-name">shortcuts</property>
                <child>
                  <object class="GtkShortcutsGroup">
                    <property name="title" translatable="yes">General</property>
                    <child>
                      <object class="GtkShortcutsShortcut">
                        <property name="title" translatable="yes">Show Shortcuts</property>
                        <property name="accelerator">&lt;Primary&gt;question</property>
                      </object>
                    </child>
                    <child>
                      <object class="GtkShortcutsShortcut">
                        <property name="title" translatable="yes">Export</property>
                        <property name="accelerator">&lt;Primary&gt;e</property>
                      </object>
                    </child>
                    <child>
                      <object class="GtkShortcutsShortcut">
                        <property name="title" translatable="yes">Refresh</property>
                        <property name="accelerator">F5</property>
                      </object>
                    </child>
                    <child>
                      <object class="GtkShortcutsShortcut">
                        <property name="title" translatable="yes">Publish (when panel open)</property>
                        <property name="accelerator">&lt;Primary&gt;Return</property>
                      </object>
                    </child>
                    <child>
                      <object class="GtkShortcutsShortcut">
                        <property name="title" translatable="yes">Quit</property>
                        <property name="accelerator">&lt;Primary&gt;q</property>
                      </object>
                    </child>
                  </object>
                </child>
              </object>
            </child>
          </object>
        </interface>
        ''')
        shortcuts = builder.get_object("shortcuts")
        shortcuts.set_transient_for(self)
        shortcuts.present()

    # --- Export ------------------------------------------------------------

    def do_export(self, action, param):
        # Gather all messages or current topic
        if self._selected_topic:
            msgs = list(self._messages.get(self._selected_topic, []))
        else:
            msgs = []
            for topic_msgs in self._messages.values():
                msgs.extend(topic_msgs)
        # Strip non-serializable bytes
        clean = []
        for m in msgs:
            clean.append({k: v for k, v in m.items() if k != "payload_bytes"})
        dialog = ExportDialog(clean)
        dialog.present(self)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="se.danielnylander.mqtt-inspector")

    def do_activate(self):
        window = self.props.active_window
        if not window:
            window = MainWindow(application=self)
        window.present()

    def do_startup(self):
        Adw.Application.do_startup(self)

        # Actions
        actions = [
            ("quit", self.quit_app),
            ("about", self.show_about),
            ("shortcuts", self.show_shortcuts),
            ("refresh", self.refresh_data),
            ("export", self.do_export),
        ]
        for name, cb in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)

        # Keyboard shortcuts
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("app.shortcuts", ["<Primary>question"])
        self.set_accels_for_action("app.refresh", ["F5"])
        self.set_accels_for_action("app.export", ["<Primary>e"])

    def quit_app(self, action, param):
        win = self.props.active_window
        if win and hasattr(win, '_client') and win._client:
            win._disconnect()
        self.quit()

    def show_about(self, action, param):
        window = self.props.active_window
        if window:
            window.show_about(action, param)

    def show_shortcuts(self, action, param):
        window = self.props.active_window
        if window:
            window.show_shortcuts(action, param)

    def refresh_data(self, action, param):
        window = self.props.active_window
        if window:
            window._set_status(_("Refreshing…"))
            GLib.timeout_add_seconds(1, lambda: window._set_status(_("Ready")))

    def do_export(self, action, param):
        window = self.props.active_window
        if window:
            window.do_export(action, param)


def main():
    app = Application()
    return app.run(sys.argv)


if __name__ == '__main__':
    main()
