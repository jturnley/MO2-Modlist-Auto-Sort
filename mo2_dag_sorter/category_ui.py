"""Ask what a mod is, where nothing on disk could say.

Only ever shown for mods that have no category from any source, so every
row here is currently placed by a guess made from the file tree.  The guess
is shown next to each one, and left selected: a user who does not know the
answer for a particular mod should be able to close this without making the
list worse, and the fastest way to say "I do not know" is to change nothing.

Answers are kept in user_rules.json by mod name and outrank every other
source of a category from then on, so this is asked once per mod rather
than once per sort.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                             QDialogButtonBox, QHeaderView, QLabel,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from .tiers import CATEGORY_NAME_TIERS

KEEP = "- leave it where the guess put it -"


def choices() -> list[str]:
    """Every category the tier table understands, tidied for a menu."""
    return sorted({name.title() for name in CATEGORY_NAME_TIERS})


class CategoryDialog(QDialog):
    """One row per mod, with the guess pre-selected."""

    def __init__(self, unknown, decisions, parent=None) -> None:
        super().__init__(parent)
        self._unknown = list(unknown)
        self._decisions = decisions
        self._boxes: list[QComboBox] = []
        self.setWindowTitle("Mods with no category")
        self.resize(1050, 560)

        layout = QVBoxLayout(self)
        off_nexus = sum(1 for u in self._unknown if u.off_nexus)
        layout.addWidget(QLabel(
            "{} mod(s) have no category from Nexus or MO2, so the sorter "
            "placed them by looking at what files they ship. {} of those "
            "were not installed from Nexus at all.\n\nTelling it what they "
            "are will place them properly. Anything left alone keeps the "
            "guess, and you will be asked again next time.".format(
                len(self._unknown), off_nexus), self))

        table = QTableWidget(len(self._unknown), 4, self)
        table.setHorizontalHeaderLabels(
            ["Mod", "Why it is unknown", "Placed by", "Select a Category"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.verticalHeader().setVisible(False)
        for row, item in enumerate(self._unknown):
            table.setItem(row, 0, QTableWidgetItem(item.name))
            table.setItem(row, 1, QTableWidgetItem(item.why))
            table.setItem(row, 2, QTableWidgetItem(item.guess))
            box = QComboBox(self)
            box.addItem(KEEP)
            box.addItems(choices())
            already = decisions.category_for(item.name)
            if already:
                at = box.findText(already.title())
                if at >= 0:
                    box.setCurrentIndex(at)
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToContents)
            table.setCellWidget(row, 3, box)
            self._boxes.append(box)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        # Wide enough for the longest category name plus the drop-down
        # arrow: the point of the column is reading the names in it.
        widest = max(choices() + [KEEP], key=len)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(
            3, table.fontMetrics().horizontalAdvance(widest) + 60)
        layout.addWidget(table, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Save and re-sort")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            "Skip - keep the guesses")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        for item, box in zip(self._unknown, self._boxes):
            chosen = box.currentText()
            if chosen != KEEP:
                self._decisions.set_category(item.name, chosen)
        self._decisions.save()
        self.accept()
