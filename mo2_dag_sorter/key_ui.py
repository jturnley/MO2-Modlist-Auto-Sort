"""Somewhere to put a Nexus API key without going hunting for it.

MO2 holds a Nexus connection of its own and will not hand the credential to
a plugin, so the sorter borrows the connection instead - see
:mod:`nexus_bridge`.  That works on most builds and is the path nobody has
to configure.  When it does not work, the alternative is the user's own key,
and until now the only place to put one was Settings -> Plugins, several
clicks deep, with nothing to say whether what was typed there was any good.

The key is written to the same plugin setting as before.  Nothing here
writes it anywhere else, and it is never shown back in full: once it is
stored, the dialog reports only that a key is present and its last four
characters, which is enough to tell two keys apart and not enough to use.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QPushButton,
                             QVBoxLayout)

from . import keys, nexus
from .keys import OWNER, SETTING, masked


class KeyDialog(QDialog):
    """Enter, test, or remove the key the sorter falls back to."""

    def __init__(self, organizer, domain: str, bridge_works: bool,
                 parent=None) -> None:
        super().__init__(parent)
        self._organizer = organizer
        self._domain = domain
        self.setWindowTitle("Nexus API Key")
        self.resize(620, 0)

        stored = str(organizer.pluginSetting(OWNER, SETTING) or "").strip()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self._status(stored, bridge_works), self))

        entry = QLabel("Key:", self)
        self._field = QLineEdit(self)
        self._field.setEchoMode(QLineEdit.EchoMode.Password)
        self._field.setPlaceholderText(
            "Paste a key here to override MO2's own connection")
        # Pre-filling would put the credential back on screen for no reason;
        # the status line above already says whether one is stored.
        row = QHBoxLayout()
        row.addWidget(entry)
        row.addWidget(self._field, 1)
        layout.addLayout(row)

        show = QCheckBox("Show what I am typing", self)
        show.toggled.connect(
            lambda on: self._field.setEchoMode(
                QLineEdit.EchoMode.Normal if on
                else QLineEdit.EchoMode.Password))
        layout.addWidget(show)

        layout.addWidget(QLabel(
            "Get one from nexusmods.com -> your profile -> Site preferences "
            "-> API keys.\nIt is stored in MO2's own plugin settings, not in "
            "the mod list.", self))

        why = QLabel(
            "Why a plugin might want its own key:\n\n"
            "MO2's built-in connection speaks the Nexus v1 API, and hands "
            "a plugin only what a v1 mod record holds - name, category, "
            "version and counters. It will not pass its credential on, so "
            "a plugin cannot ask Nexus anything MO2 did not already ask "
            "for. Newer Nexus APIs carry much more: requirements, "
            "per-file data, collections. A key here is a credential "
            "plugins can use directly, for v1 endpoints MO2 does not "
            "expose and for newer queries that need authenticating.\n\n"
            "This sorter reads requirements from the v2 GraphQL API, "
            "which needs no key; the key is what lets it fetch a game's "
            "category table from v1 when MO2's connection is not "
            "available.\n\n"
            "Better still, install the MO2 Nexus API Extender: it keeps "
            "one key for every plugin, encrypted for your Windows "
            "account, instead of a plain-text copy per plugin here. "
            "This sorter uses it automatically when it is present.",
            self)
        why.setWordWrap(True)
        layout.addWidget(why)

        self._result = QLabel("", self)
        self._result.setWordWrap(True)
        layout.addWidget(self._result)

        buttons = QDialogButtonBox(self)
        test = QPushButton("Test", self)
        test.clicked.connect(self._test)
        buttons.addButton(test, QDialogButtonBox.ButtonRole.ActionRole)
        if stored:
            clear = QPushButton("Remove stored key", self)
            clear.clicked.connect(self._clear)
            buttons.addButton(clear,
                              QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Save)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _status(stored: str, bridge_works: bool) -> str:
        if stored:
            return ("A key of your own is stored ({}) and is being used "
                    "instead of MO2's connection.".format(masked(stored)))
        if bridge_works:
            return ("MO2's own Nexus connection is working, and the sorter "
                    "is using it. You only need a key here if that stops "
                    "being enough.")
        return ("MO2's own Nexus connection is not available to the sorter "
                "on this build. Without a key here, categories and "
                "requirements come from what is already cached on disk.")

    def _typed(self) -> str:
        return self._field.text().strip()

    def _test(self) -> None:
        key = self._typed() or str(
            self._organizer.pluginSetting(OWNER, SETTING) or "").strip()
        if not key:
            self._result.setText("Nothing to test - the box is empty and no "
                                 "key is stored.")
            return
        self._result.setText("Checking...")
        self._result.repaint()
        try:
            categories = nexus.fetch_categories(self._domain, key)
        except Exception as exc:                  # any network or HTTP error
            self._result.setText(
                "That key did not work: {}".format(keys.reason(exc)))
            return
        self._result.setText(
            "Works - Nexus returned {} categories for {}.".format(
                len(categories), self._domain))

    def _save(self) -> None:
        key = self._typed()
        if not key:
            QMessageBox.information(
                self, "Nexus API Key",
                "Nothing was typed, so nothing has been changed.")
            return
        self._organizer.setPluginSetting(OWNER, SETTING, key)
        QMessageBox.information(
            self, "Nexus API Key",
            "Saved ({}). The sorter will use this instead of MO2's own "
            "connection.".format(masked(key)))
        self.accept()

    def _clear(self) -> None:
        if QMessageBox.question(
                self, "Nexus API Key",
                "Remove the stored key? The sorter will go back to using "
                "MO2's own Nexus connection.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        self._organizer.setPluginSetting(OWNER, SETTING, "")
        QMessageBox.information(self, "Nexus API Key",
                                "Removed. MO2's own connection will be used.")
        self.accept()
