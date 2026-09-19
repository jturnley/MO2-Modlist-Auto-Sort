"""Add the sorter's two entries to MO2's mod list right-click menu.

MO2 has no plugin API for this.  A tool plugin gets a place in the Tools
menu and nothing else, and the left pane's context menu is built in C++
inside ``ModListView::contextMenuEvent``.  So the entries are appended to
the menu after MO2 has built it: watch the view for a context-menu event,
let MO2 open its own menu, and add to whatever popup appears.

That is a reach into another program's widgets and it is written to fail
quietly.  If MO2 renames the view, restructures the menu, or the popup is
not where it is expected, nothing is added and nothing breaks - both actions
remain available from the Tools menu.  Nothing MO2 built is removed or
reordered; this only appends.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMenu, QTreeView

VIEW_NAMES = ("modList", "modListView", "listMods")


def find_view(window):
    """MO2's left pane, by the name its .ui file gives it."""
    for name in VIEW_NAMES:
        view = window.findChild(QTreeView, name)
        if view is not None:
            return view
    # Falling back on shape rather than name: the left pane is the tree
    # whose model has a mod count in it. Better than giving up if MO2
    # renames the widget in a later version.
    for view in window.findChildren(QTreeView):
        if view.objectName().lower().startswith("mod"):
            return view
    return None


class MenuFilter(QObject):
    """Appends actions to the mod list's context menu as it opens.

    ``build`` is called with the list of selected mod names and the menu, and
    adds whatever it wants to it.
    """

    def __init__(self, view, build) -> None:
        super().__init__(view)
        self._view = view
        self._build = build

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.ContextMenu:
            # MO2 builds and opens its menu while handling this event, so the
            # popup does not exist yet. Queue the append for the moment it
            # does, and never consume the event.
            QTimer.singleShot(0, self._append)
        return False

    def _selected(self) -> list[str]:
        model = self._view.selectionModel()
        if model is None:
            return []
        names = []
        for index in model.selectedRows():
            value = index.data(Qt.ItemDataRole.DisplayRole)
            if value:
                names.append(str(value))
        return names

    def _append(self) -> None:
        menu = QApplication.activePopupWidget()
        if not isinstance(menu, QMenu):
            return
        names = self._selected()
        if not names:
            return
        try:
            self._build(names, menu)
        except Exception:                  # never break MO2's own menu
            pass


def install(window, build):
    """Hook the menu. Returns the filter, or None if the view was not found."""
    view = find_view(window)
    if view is None:
        return None
    hook = MenuFilter(view, build)
    view.viewport().installEventFilter(hook)
    view.installEventFilter(hook)
    return hook
