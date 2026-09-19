"""The dialog that asks which way round an unresolvable pair should go.

One row per conflict the sorter had to settle by rule.  Each row starts on
the sorter's own answer, so leaving the dialog alone keeps exactly the
behaviour of not having opened it - the point is to let the user disagree,
not to make them adjudicate a list of things they do not care about.

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
        layout.addWidget(QLabel(
            "Each of these pairs makes two demands that cannot both be met. "
            "The sorter picked one; choose the other if it has it backwards.\n"
            "The mod on the left loads first, which means it sits higher in "
            "the left pane and LOSES the conflict." + chr(10) +
            "Each mod's Nexus file date and version are shown to help: where "
            "two mods replace each other's files, the one built later is "
            "usually the one meant to win, and a mod's own page often says "
            "outright which it expects to overwrite."))

        self.table = QTableWidget(len(self._conflicts), 3, self)
        self.table.setHorizontalHeaderLabels(
            ["Loads first (loses)", "Loads second (wins)", "Why they clash"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        for row, edge in enumerate(self._conflicts):
            group = QButtonGroup(self)
            # The sorter dropped this edge, so its answer is the opposite of
            # what the edge asks for: the child loads first.
            self.table.setCellWidget(row, 0, self._cell(
                group, edge.child, checked=True))
            self.table.setCellWidget(row, 1, self._cell(
                group, edge.parent, checked=False))
            cell = QTableWidgetItem(WHY.get(edge.reason, edge.reason))
            cell.setToolTip(edge.detail or "")
            self.table.setItem(row, 2, cell)
            self._groups.append(group)

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

    def _cell(self, group: QButtonGroup, name: str, checked: bool) -> QWidget:
        widget = QWidget(self)
        box = QHBoxLayout(widget)
        box.setContentsMargins(4, 0, 4, 0)
        button = QRadioButton(self._label(name), widget)
        button.setChecked(checked)
        button.setProperty("mod_name", name)
        group.addButton(button)
        box.addWidget(button)
        link = self._link(name)
        if link is not None:
            box.addWidget(link)
        box.addStretch(1)
        return widget

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
        for edge, group in zip(self._conflicts, self._groups):
            chosen = group.checkedButton()
            if chosen is None:
                continue
            first = str(chosen.property("mod_name"))
            second = edge.parent if first == edge.child else edge.child
            self._decisions.set(first, second,
                                "was: {}".format(WHY.get(edge.reason,
                                                         edge.reason)))
            count += 1
        if count:
            self._decisions.save()
        return count
