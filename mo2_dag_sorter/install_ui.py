"""The two windows for what happens to a newly installed mod.

``ask`` is the pop-up after an install; ``OptionsDialog`` is the Tools
menu entry for changing the answer later. The rules are in
:mod:`install_action`.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QDialog,
                             QDialogButtonBox, QLabel, QMessageBox,
                             QRadioButton, QVBoxLayout)

from . import install_action as act
from ._i18n import translate


def ask(mod_name: str, already_enabled: bool, parent=None) -> tuple[str, bool]:
    """(action, remember it) for one installed mod.

    Closing the box is "do nothing" and is never remembered: a window
    dismissed in a hurry should not decide every install after it.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(translate("InstallDialog", "New mod installed"))
    remember = None
    if already_enabled:
        # A reinstall or update of a mod that is already on: there is
        # nothing to switch on, so the only question is whether to place it
        # again - and that is not a choice to make for every future install.
        box.setText(translate(
            "InstallDialog", "{} has been reinstalled and is already "
            "enabled.\n\nPlace it again where the sorter says it belongs?"
        ).format(mod_name))
        sort = box.addButton(translate("InstallDialog", "Sort it"),
                             QMessageBox.ButtonRole.AcceptRole)
        nothing = box.addButton(translate("InstallDialog",
                                          "Leave it where it is"),
                                QMessageBox.ButtonRole.RejectRole)
        enable = None
    else:
        box.setText(translate(
            "InstallDialog", "{} has been installed. MO2 leaves new mods "
            "disabled at the bottom of the list.\n\nWhat should happen to "
            "it?").format(mod_name))
        box.setInformativeText(translate(
            "InstallDialog", "You can change this later from the Tools "
            "menu: New Mod Options."))
        sort = box.addButton(translate("InstallDialog", "Enable and sort"),
                             QMessageBox.ButtonRole.AcceptRole)
        enable = box.addButton(translate("InstallDialog", "Enable only"),
                               QMessageBox.ButtonRole.ActionRole)
        nothing = box.addButton(translate("InstallDialog", "Do nothing"),
                                QMessageBox.ButtonRole.RejectRole)
        remember = QCheckBox(translate(
            "InstallDialog", "Do this automatically from now on"))
        box.setCheckBox(remember)
    box.setDefaultButton(sort)
    box.setEscapeButton(nothing)
    box.exec()

    clicked = box.clickedButton()
    if clicked is sort:
        action = act.ENABLE_SORT
    elif enable is not None and clicked is enable:
        action = act.ENABLE
    else:
        action = act.NOTHING
    keep = (remember is not None and remember.isChecked()
            and clicked in (sort, enable, nothing))
    return action, keep


class OptionsDialog(QDialog):
    """Choose what happens to new mods from now on."""

    def __init__(self, current: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(translate("InstallOptions", "New Mod Options"))
        self.choice = act.normalise(current)

        layout = QVBoxLayout(self)
        intro = QLabel(translate(
            "InstallOptions", "When MO2 finishes installing a mod, it leaves "
            "it disabled at the bottom of the list. What should the sorter "
            "do then?"), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._group = QButtonGroup(self)
        self._buttons = {}
        for action in act.CHOICES:
            button = QRadioButton(act.label(action), self)
            button.setChecked(action == self.choice)
            self._group.addButton(button)
            self._buttons[action] = button
            layout.addWidget(button)

        note = QLabel(translate(
            "InstallOptions", "Sorting places only the new mod; the rest of "
            "the list keeps its order. A mod that is reinstalled while "
            "enabled is sorted again only with \"Enable it and sort it\"."),
            self)
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        for action, button in self._buttons.items():
            if button.isChecked():
                self.choice = action
        self.accept()
