"""Pick a mod list to go back to.

Deliberately plain: a list of dated copies, what caused each one, and how
many mods it held.  The mod count is what actually tells two backups apart
at a glance - a list that went from 913 entries to 894 lost something.

"Undo last change" is the same operation with the choice already made, put
where a panicking user will find it first.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
                             QLabel, QListWidget, QListWidgetItem,
                             QVBoxLayout)


class RestoreDialog(QDialog):
    """Returns the chosen :class:`backups.Backup` in :attr:`chosen`."""

    def __init__(self, found, current_mods: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Restore a previous mod list")
        self.resize(620, 420)
        self.chosen = None
        self._found = list(found)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "The list in use now holds {} mods. Pick the version to go back "
            "to - the newest is at the top.".format(current_mods)))

        self.list = QListWidget(self)
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        for backup in self._found:
            QListWidgetItem(backup.headline, self.list)
        if self._found:
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.list)

        layout.addWidget(QLabel(
            "Restoring saves the current list first, so this can be undone "
            "as well."))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Restore")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._found):
            self.chosen = self._found[row]
        super().accept()
