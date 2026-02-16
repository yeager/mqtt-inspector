#!/usr/bin/env python3

import sys
import gettext
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, GLib

# Set up gettext
TEXTDOMAIN = 'mqtt-inspector'
gettext.textdomain(TEXTDOMAIN)
gettext.bindtextdomain(TEXTDOMAIN, '/usr/share/locale')
_ = gettext.gettext

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.set_default_size(800, 600)
        self.set_title(_(UMQTT))
        
        # Create header bar
        header = Adw.HeaderBar()
        self.set_titlebar(header)
        
        # Create main content
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)
        
        # Status bar
        self.status_label = Gtk.Label(label=_("Ready"))
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.add_css_class("dim-label")
        box.append(self.status_label)
        
    def show_about(self, action, param):
        about = Adw.AboutWindow(transient_for=self)
        about.set_application_name(_(UMQTT))
        about.set_application_icon("se.danielnylander.mqtt-inspector")
        about.set_developer_name("Daniel Nylander")
        about.set_version("0.1.0")
        about.set_website("https://github.com/yeager/mqtt-inspector")
        about.set_translator_credits(_("translator-credits"))
        about.present()
        
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
                        <property name="title" translatable="yes">Refresh</property>
                        <property name="accelerator">F5</property>
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
        
        # Create actions
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self.quit_app)
        self.add_action(quit_action)
        
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self.show_about)
        self.add_action(about_action)
        
        shortcuts_action = Gio.SimpleAction.new("shortcuts", None)
        shortcuts_action.connect("activate", self.show_shortcuts)
        self.add_action(shortcuts_action)
        
        refresh_action = Gio.SimpleAction.new("refresh", None)
        refresh_action.connect("activate", self.refresh_data)
        self.add_action(refresh_action)
        
        # Set keyboard shortcuts
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("app.shortcuts", ["<Primary>question"])
        self.set_accels_for_action("app.refresh", ["F5"])
        
    def quit_app(self, action, param):
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
            window.status_label.set_text(_("Refreshing..."))
            # Add refresh logic here
            GLib.timeout_add_seconds(1, lambda: window.status_label.set_text(_("Ready")))

def main():
    app = Application()
    return app.run(sys.argv)

if __name__ == '__main__':
    main()
