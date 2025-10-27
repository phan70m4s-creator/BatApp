# -*- coding: utf-8 -*-
import os
import json
import sqlite3
from datetime import datetime

import phonenumbers
from phonenumbers import carrier, timezone
from plyer import permission

from kivy.lang import Builder
from kivy.clock import Clock
from kivy.properties import StringProperty, ListProperty

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDRaisedButton
from kivymd.uix.dialog import MDDialog
from kivymd.uix.list import OneLineAvatarIconListItem, IconLeftWidget
from kivymd.uix.spinner import MDSpinner
from kivymd.uix.snackbar import Snackbar


KV = '''
BoxLayout:
    orientation: 'vertical'

    MDToolbar:
        title: "OSINT‑Lite"
        left_action_items: [["menu", lambda x: app.open_menu()]]
        right_action_items: [["content-copy", lambda x: app.copy_number()]]

    ScrollView:
        MDList:
            id: contact_list

    AnchorLayout:
        anchor_x: 'right'
        anchor_y: 'bottom'
        MDRaisedButton:
            text: "Refresh Contacts"
            on_release: app.load_contacts()
            md_bg_color: app.theme_cls.primary_color
            padding: "20dp", "20dp"
'''


# ----------------------------------------------------------------------
# SQLite cache helper
# ----------------------------------------------------------------------
class CacheDB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self._create_table()

    def _create_table(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS osint (
                phone TEXT PRIMARY KEY,
                formatted TEXT,
                valid INTEGER,
                carrier TEXT,
                timezone TEXT,
                cached_at TEXT
            )
        ''')
        self.conn.commit()

    def get(self, phone):
        cur = self.conn.execute(
            "SELECT formatted, valid, carrier, timezone, cached_at FROM osint WHERE phone=?",
            (phone,))
        row = cur.fetchone()
        return row

    def set(self, phone, formatted, valid, carrier_name, tz):
        self.conn.execute('''
            INSERT OR REPLACE INTO osint
            (phone, formatted, valid, carrier, timezone, cached_at)
            VALUES (?,?,?,?,?,?)
        ''', (phone, formatted, int(valid), carrier_name, tz,
              datetime.utcnow().isoformat()))
        self.conn.commit()


# ----------------------------------------------------------------------
# UI list item
# ----------------------------------------------------------------------
class ContactItem(OneLineAvatarIconListItem):
    phone = StringProperty()
    icon = StringProperty("account")

    def on_release(self):
        app = MDApp.get_running_app()
        app.selected_contact = self.phone
        app.show_contact_details()


# ----------------------------------------------------------------------
# Main application
# ----------------------------------------------------------------------
class OSINTLiteApp(MDApp):
    selected_contact = StringProperty("")
    contact_details = ListProperty([])

    def build(self):
        self.theme_cls.primary_palette = "DeepPurple"
        self.db_path = os.path.join(self.user_data_dir, "osint_cache.db")
        self.cache = CacheDB(self.db_path)
        return Builder.load_string(KV)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_start(self):
        self.show_disclaimer()
        self.request_contacts_permission()
        Clock.schedule_once(lambda dt: self.load_contacts(), 0.5)

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------
    def request_contacts_permission(self):
        if not permission.check_permission('android.permission.READ_CONTACTS'):
            permission.request_permissions(['android.permission.READ_CONTACTS'])

    # ------------------------------------------------------------------
    # Disclaimer
    # ------------------------------------------------------------------
    def show_disclaimer(self):
        flag = os.path.join(self.user_data_dir, "disclaimer_accepted")
        if os.path.exists(flag):
            return

        content = MDBoxLayout(orientation='vertical', spacing='12dp')
        content.add_widget(
            MDDialog(
                text=(
                    "This app only uses **local** data (phone number "
                    "validation, carrier, timezone) and stores the results "
                    "in a private SQLite cache on your device. No network "
                    "calls are performed. Use responsibly."
                ),
                size_hint=(1, None),
                height="150dp"
            )
        )
        dlg = MDDialog(
            title="Disclaimer",
            type="custom",
            content_cls=content,
            buttons=[
                MDRaisedButton(
                    text="I Agree",
                    on_release=lambda x: self._accept_disclaimer(dlg, flag)
                )
            ],
            size_hint=(0.9, None),
            height="250dp"
        )
        dlg.open()

    def _accept_disclaimer(self, dialog, flag_path):
        open(flag_path, "w").write("accepted")
        dialog.dismiss()

    # ------------------------------------------------------------------
    # Snackbar helper
    # ------------------------------------------------------------------
    def show_snackbar(self, text):
        Snackbar(text=text, duration=3).open()

    # ------------------------------------------------------------------
    # Contact loading
    # ------------------------------------------------------------------
    def load_contacts(self):
        self.show_spinner()
        try:
            from jnius import autoclass, cast

            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity
            ContentResolver = activity.getContentResolver()

            ContactsContract_Contacts = autoclass('android.provider.ContactsContract$Contacts')
            ContactsContract_CommonDataKinds_Phone = autoclass(
                'android.provider.ContactsContract$CommonDataKinds$Phone')
            Cursor = ContentResolver.query(
                ContactsContract_Contacts.CONTENT_URI,
                None,
                None,
                None,
                None)

            contacts = []
            if Cursor.moveToFirst():
                while True:
                    contact_id = Cursor.getString(
                        Cursor.getColumnIndex(ContactsContract_Contacts._ID))
                    display_name = Cursor.getString(
                        Cursor.getColumnIndex(ContactsContract_Contacts.DISPLAY_NAME))

                    # Query phone numbers for this contact
                    phone_cursor = ContentResolver.query(
                        ContactsContract_CommonDataKinds_Phone.CONTENT_URI,
                        None,
                        f"{ContactsContract_CommonDataKinds_Phone.CONTACT_ID}=?",
                        [contact_id],
                        None)

                    if phone_cursor.moveToFirst():
                        while True:
                            number = phone_cursor.getString(
                                phone_cursor.getColumnIndex(
                                    ContactsContract_CommonDataKinds_Phone.NUMBER))
                            contacts.append({"name": display_name, "phone": number})
                            if not phone_cursor.moveToNext():
                                break
                    phone_cursor.close()

                    if not Cursor.moveToNext():
                        break
            Cursor.close()

            # Populate UI
            self.root.ids.contact_list.clear_widgets()
            for c in contacts:
                item = ContactItem(text=c["name"], phone=c["phone"])
                item.add_widget(IconLeftWidget(icon=item.icon))
                self.root.ids.contact_list.add_widget(item)

        except Exception as e:
            # Fallback to a tiny mock list if something goes wrong (e.g., running on desktop)
            self.show_snackbar(f"Error loading contacts: {e}")
            self.root.ids.contact_list.clear_widgets()
            mock = [
                {"name": "John Doe", "phone": "+11234567890"},
                {"name": "Jane Smith", "phone": "+449876543210"},
                {"name": "Emergency", "phone": "+911"}
            ]
            for c in mock:
                item = ContactItem(text=c["name"], phone=c["phone"])
                item.add_widget(IconLeftWidget(icon=item.icon))
                self.root.ids.contact_list.add_widget(item)

        finally:
            self.hide_spinner()

    # ------------------------------------------------------------------
    # Clipboard copy
    # ------------------------------------------------------------------
    def copy_number(self):
        if self.selected_contact:
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(self.selected_contact)
            self.show_snackbar(f"Copied: {self.selected_contact}")

    # ------------------------------------------------------------------
    # Loading spinner
    # ------------------------------------------------------------------
    def show_spinner(self):
        self.spinner = MDSpinner(
            size_hint=(None, None),
            size=("48dp", "48dp"),
            pos_hint={"center_x": .5, "center_y": .5}
        )
        self.root.add_widget(self.spinner)

    def hide_spinner(self):
        if hasattr(self, "spinner"):
            self.root.remove_widget(self.spinner)

    # ------------------------------------------------------------------
    # OSINT details (device‑only, cached)
    # ------------------------------------------------------------------
    def show_contact_details(self):
        if not self.selected_contact:
            return

        phone = self.selected_contact
        cached = self.cache.get(phone)

        if cached:
            formatted, valid, carr, tz, cached_at = cached
            self.contact_details = [
                f"Phone: {formatted}",
                f"Valid: {'Yes' if valid else 'No'}",
                f"Carrier: {carr or 'Unknown'}",
                f"Timezone: {tz or 'Unknown'}",
                f"Cached at: {cached_at}"
            ]
            self.show_details_dialog()
            return

        # No cache – compute locally
        try:
            parsed = phonenumbers.parse(phone, None)
            formatted = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
            valid = phonenumbers.is_valid_number(parsed)
            carr = carrier.name_for_number(parsed, "en")
            tz_list = timezone.time_zones_for_number(parsed)
            tz = tz_list[0] if tz_list else ""
        except Exception:
            formatted = phone
            valid = False
            carr = ""
            tz = ""

        # Store in cache
        self.cache.set(phone, formatted, valid, carr, tz)

        self.contact_details = [
            f"Phone: {formatted}",
            f"Valid: {'Yes' if valid else 'No'}",
            f"Carrier: {carr or 'Unknown'}",
            f"Timezone: {tz or 'Unknown'}",
            "Data fetched locally – no external request"
        ]
        self.show_details_dialog()

    # ------------------------------------------------------------------
    # Details dialog
    # ------------------------------------------------------------------
def show_details_dialog(self):
    if hasattr(self, "dialog") and self.dialog:
        self.dialog.dismiss()

    content = MDBoxLayout(orientation='vertical', spacing='8dp', padding='10dp')
    for line in self.contact_details:
        content.add_widget(
            OneLineAvatarIconListItem(text=line)
        )



        self.dialog = MDDialog(
            title="OSINT Details",
            type="custom",
            content_cls=content,
            buttons=[
                MDRaisedButton(
                    text="Close",
                    on_release=lambda x: self.dialog.dismiss()
                )
            ],
            size_hint=(0.9, None),
            height="400dp"
        )
        self.dialog.open()

    # ------------------------------------------------------------------
    # Menu (About / Exit)
    # ------------------------------------------------------------------
    def open_menu(self):
        from kivymd.uix.menu import MDDropdownMenu

        menu_items = [
            {
                "viewclass": "OneLineListItem",
                "text": "About",
                "on_release": lambda x="About": self.show_about()
            },
            {
                "viewclass": "OneLineListItem",
                "text": "Exit",
                "on_release": lambda x="Exit": self.stop()
            },
        ]
        self.menu = MDDropdownMenu(
            caller=self.root.ids.toolbar,
            items=menu_items,
            width_mult=4,
        )
        self.menu.open()

    def show_about(self):
        MDDialog(
            title="OSINT‑Lite",
            text=(
                "A privacy‑first utility that extracts phone‑number "
                "information locally and caches results in SQLite. "
                "No network calls are performed."
            ),
            buttons=[
                MDRaisedButton(text="OK", on_release=lambda x: x.parent.parent.dismiss())
            ],
            size_hint=(0.8, None),
            height="200dp"
        ).open()


if __name__ == "__main__":
    OSINTLiteApp().run()

