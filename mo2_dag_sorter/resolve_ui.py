"""The dialog that asks which way round an unresolvable pair should go.

One row per conflict the sorter had to settle by rule.  Each row starts on
the sorter's own answer, so leaving the dialog alone keeps exactly the
behaviour of not having opened it - the point is to let the user disagree,
not to make them adjudicate a list of things they do not care about.

The question asked is "which of these should win", because that is the
question a user actually has an opinion about.  Winning means loading
last: MO2's left pane loads top to bottom and the later mod overwrites
the earlier one, so the winner is placed lower.  The selected mod is
always drawn in the right-hand column, and selecting the one on the left
moves it across - so the column headings never end up describing the
opposite of what was chosen, which is what they did before.

An answer is stored against the two mod names and reused for ever after, so
a pair only ever asks once.

Deliberately not automated: a pair like "Heavy Armory - New Weapons" and
"Heavy Armory - Glaive Addons" names each other as masters, and the thing
that settles it is what the FOMOD offered to install and which file was
built later.  Nothing readable off disk decides that, so the dialog shows
the file dates and versions it does know and leaves the call to the user.

Often the answer is on the Nexus page and nowhere else - "Caesia Ostim -
Sequenced" lists "Caesia Ostim Patch" as a requirement and tells you to
overwrite it, which is a sentence of English prose on a web page.  So each
mod gets a link to its own page: the dialog cannot read that, but it can
put the user one click away from it.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox,
                             QDialog, QDialogButtonBox, QHeaderView, QLabel,
                             QRadioButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget, QHBoxLayout)

BREAK = chr(10)          # newline, spelled out to keep escapes out of the UI text

WHY = {
    "master_requirement": "declares the other as a master",
    "asset_path": "ships files under the other's plugin folder",
    "file_conflict": "overlapping files",
    "user_rule": "your earlier choice",
    "nexus_requirement": "listed as a requirement on Nexus",
}


class ResolveDialog(QDialog):
    """Ask about each conflict; the answers land in ``decisions``."""

    def __init__(self, conflicts, decisions, parent=None, nodes=(),
                 domain: str = "skyrimspecialedition") -> None:
        super().__init__(parent)
        self.setWindowTitle("Conflicts the sorter had to settle")
        self.resize(900, 420)
        self._conflicts = list(conflicts)
        self._decisions = decisions
        self._nodes = {n.name: n for n in nodes}
        self._domain = domain
        self._groups: list[QButtonGroup] = []

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Each of these pairs makes two demands that cannot both be met."
            + BREAK + BREAK +
            "Select the mod that should WIN - the one whose files you "
            "want to keep. It is placed lower in the left pane, so it "
            "loads last and overwrites the other."
            + BREAK + BREAK +
            "The mod you select is always the one shown on the right. "
            "Picking the mod on the left moves it across, so the columns "
            "always describe what you are actually going to get. Rows "
            "start on the sorter's own answer, so leaving a row alone "
            "keeps it."
            + BREAK + BREAK +
            "File dates and versions are shown to help: where two mods "
            "replace each other's files the one built later is usually the "
            "one meant to win, and a mod's own page often says outright "
            "which it expects to overwrite.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(len(self._conflicts), 3, self)
        self.table.setHorizontalHeaderLabels(
            ["Loads first - gets overwritten",
             "Loads last - WINS (selected)",
             "Why they clash"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        # One winner per row, held by name. The sorter dropped this edge,
        # so its answer is that the child loads first and the parent wins.
        self._winners: list[str] = [e.parent for e in self._conflicts]

        for row, edge in enumerate(self._conflicts):
            self._groups.append(QButtonGroup(self))
            self._render_row(row)
            cell = QTableWidgetItem(WHY.get(edge.reason, edge.reason))
            cell.setToolTip(edge.detail or "")
            self.table.setItem(row, 2, cell)

        self.table.resizeRowsToContents()
        layout.addWidget(self.table)
        self.remember = QCheckBox("Remember these answers for future sorts")
        self.remember.setChecked(True)
        layout.addWidget(self.remember)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def loser_for(self, row: int) -> str:
        """The other half of the pair - whichever one is not winning."""
        edge = self._conflicts[row]
        return edge.parent if self._winners[row] == edge.child else edge.child

    def _render_row(self, row: int) -> None:
        """Draw a row so the winner is always the one on the right.

        Rebuilt rather than merely re-checked, because the two mods change
        places when the choice does. Keeping the winner in a fixed column
        is the whole point: the previous version left the names where they
        were and moved only the dot, so after a click the column headed
        "wins" stood above the mod that had just been made to lose.
        """
        group = QButtonGroup(self)
        self._groups[row] = group
        self.table.setCellWidget(row, 0, self._cell(
            group, self.loser_for(row), row, False))
        self.table.setCellWidget(row, 1, self._cell(
            group, self._winners[row], row, True))
        self.table.resizeRowToContents(row)

    def _cell(self, group: QButtonGroup, name: str, row: int,
              checked: bool) -> QWidget:
        widget = QWidget(self)
        box = QHBoxLayout(widget)
        box.setContentsMargins(4, 0, 4, 0)
        button = QRadioButton(self._label(name), widget)
        button.setChecked(checked)
        button.setProperty("mod_name", name)
        button.setToolTip("Select this mod to make it win: it will load "
                          "last and overwrite the other.")
        group.addButton(button)
        # Only the losing side is clickable into. Choosing it makes it the
        # winner, which redraws the row with it on the right.
        if not checked:
            button.clicked.connect(
                lambda _=False, r=row, n=name: self._choose(r, n))
        box.addWidget(button)
        link = self._link(name)
        if link is not None:
            box.addWidget(link)
        box.addStretch(1)
        return widget

    def _choose(self, row: int, name: str) -> None:
        if self._winners[row] == name:
            return
        self._winners[row] = name
        self._render_row(row)

    def _link(self, name: str):
        """A link to the mod's Nexus page, where the requirements are."""
        node = self._nodes.get(name)
        if node is None or not node.nexus_id:
            return None
        label = QLabel('<a href="https://www.nexusmods.com/{}/mods/{}">'
                       'Nexus</a>'.format(self._domain, node.nexus_id))
        label.setOpenExternalLinks(True)
        label.setToolTip("Open this mod's page - its requirements and "
                         "install order are stated there")
        return label

    def _label(self, name: str) -> str:
        """The mod name, plus whatever meta.ini knows about its file."""
        node = self._nodes.get(name)
        if node is None:
            return name
        bits = [b for b in (node.file_date,
                            "v" + node.version if node.version else "")
                if b]
        if not bits:
            return name
        return name + chr(10) + "   ".join(bits)

    def apply_to_decisions(self) -> int:
        """Store every answer. Returns how many were recorded."""
        if not self.remember.isChecked():
            return 0
        count = 0
        for row, edge in enumerate(self._conflicts):
            # Stated as the winner on purpose. The dialog asks which mod
            # wins, so the call recording it should use the same word -
            # the old code converted to "first" here and got it inverted.
            self._decisions.set_winner(
                self._winners[row], self.loser_for(row),
                "was: {}".format(WHY.get(edge.reason, edge.reason)))
            count += 1
        if count:
            self._decisions.save()
        return count
