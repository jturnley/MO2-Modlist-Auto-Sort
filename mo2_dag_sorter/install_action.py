"""What happens to a mod the moment MO2 finishes installing it.

MO2 installs every new mod switched off, at the bottom of the left pane.
Three things can follow, and the user picks which, once or for good:

    nothing       leave it off and where MO2 put it
    enable        switch it on, leave it where MO2 put it
    enable_sort   switch it on, then place it where the graph says it goes

or ``ask``, the default, which offers those three each time with a box to
make the answer stick.  The choice is a plugin setting, changed later from
the Tools menu.

No Qt here, so the rules can be tested; the dialogs are in
:mod:`install_ui`.
"""

from __future__ import annotations

from ._i18n import QT_TRANSLATE_NOOP, translate

SETTING = "on_install"

ASK = "ask"
NOTHING = "nothing"
ENABLE = "enable"
ENABLE_SORT = "enable_sort"

CHOICES = (ASK, NOTHING, ENABLE, ENABLE_SORT)
DEFAULT = ASK

LABELS = {
    ASK: QT_TRANSLATE_NOOP("InstallAction", "Ask me each time"),
    NOTHING: QT_TRANSLATE_NOOP("InstallAction",
                               "Do nothing - leave it disabled"),
    ENABLE: QT_TRANSLATE_NOOP("InstallAction", "Enable it only"),
    ENABLE_SORT: QT_TRANSLATE_NOOP("InstallAction", "Enable it and sort it"),
}


def normalise(value) -> str:
    """A stored setting as one of CHOICES; anything unrecognised asks.

    The setting is a free-text box in MO2's own Settings -> Plugins, so a
    typo is possible, and asking is the one answer that cannot do
    something the user did not want.
    """
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"none": NOTHING, "off": NOTHING, "disabled": NOTHING,
               "enable_only": ENABLE, "sort": ENABLE_SORT,
               "enable_and_sort": ENABLE_SORT}
    text = aliases.get(text, text)
    return text if text in CHOICES else DEFAULT


def label(action: str) -> str:
    return translate("InstallAction", LABELS[normalise(action)])


def steps(action: str, already_enabled: bool) -> tuple[bool, bool]:
    """(switch it on, sort it) for a decided action.

    A reinstall of a mod that is already on has nothing to switch on;
    sorting it again is still what ``enable_sort`` asked for.
    """
    action = normalise(action)
    if action == ENABLE_SORT:
        return not already_enabled, True
    if action == ENABLE:
        return not already_enabled, False
    return False, False
