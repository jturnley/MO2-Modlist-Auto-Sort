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

    @staticmethod
    def _open_menu():
        """The menu MO2 has just opened, if it can be found.

        activePopupWidget is the direct answer and usually right, but it
        reports whatever popup is frontmost - another plugin's menu, a
        tooltip, a combo box - so a miss there is not proof the mod list
        menu is absent. Falling back to the visible top-level QMenu costs
        one pass over a handful of widgets.
        """
        menu = QApplication.activePopupWidget()
        if isinstance(menu, QMenu):
            return menu
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMenu) and widget.isVisible():
                return widget
        return None

    def _append(self) -> None:
        menu = self._open_menu()
        if menu is None:
            return
        names = self._selected()
        if not names:
            return
        try:
            self._build(names, menu)
        except Exception:                  # never break MO2's own menu
            pass


HOOKED = "_dag_sorter_menu_hook"


def install(window, build):
    """Hook the menu. Returns the filter, or None if the view was not found."""
    view = find_view(window)
    if view is None:
        return None
    if view.property(HOOKED):
        return None                    # already hooked; do not stack filters
    hook = MenuFilter(view, build)
    view.viewport().installEventFilter(hook)
    view.installEventFilter(hook)
    view.setProperty(HOOKED, True)
    return hook


class Keeper(QObject):
    """Keeps the menu hooked, and puts it back if it ever comes off.

    A single attempt at the moment the UI is declared ready is not enough.
    The left pane is another program's widget: it may not exist yet when
    the callback runs, MO2 can rebuild it, and the order plugins are
    started in shifts when any other plugin is added or removed - so a
    hook that happened to work can stop happening for reasons that have
    nothing to do with this plugin.

    So it is checked on a timer instead of assumed. The check is one
    findChild against a property flag, which costs nothing, and it never
    stacks a second filter on a view that already has one.
    """

    FAST = 500        # while still looking for the view
    SLOW = 5000       # once hooked, just often enough to notice a rebuild
    TRIES = 40        # give the window ~20s to grow a mod list

    def __init__(self, window, build) -> None:
        super().__init__(window)
        self._window = window
        self._build = build
        self._left = self.TRIES
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._tick()

    def _tick(self) -> None:
        try:
            hooked = install(self._window, self._build) is not None
            found = hooked or find_view(self._window) is not None
        except RuntimeError:
            return                     # the window went away; nothing to do
        if found:
            self._left = self.TRIES
            self._timer.start(self.SLOW)
            return
        self._left -= 1
        if self._left > 0:
            self._timer.start(self.FAST)

    def stop(self) -> None:
        self._timer.stop()


def keep(window, build) -> Keeper:
    """Install the hook and keep it installed. The caller holds the result."""
    remember(window, build)
    return Keeper(window, build)


# What the menu needs in order to be put back, kept at module level so a
# separate tool plugin can reach it. The tools MO2 registers are separate
# objects with no handle on one another, and the one offering to repair the
# menu is not the one that installed it.
_state: dict = {"window": None, "build": None}


def remember(window, build) -> None:
    """Record what the repair entry will need. Either half may be None.

    The builder is known as soon as the sorter loads; the window only
    arrives when MO2 says its interface is up. Neither should be able to
    erase the other, so a None is a no-op rather than a reset.
    """
    if window is not None:
        _state["window"] = window
    if build is not None:
        _state["build"] = build


def _window(window=None):
    """The window to hook, preferring one we were handed."""
    if window is not None:
        return window
    known = _state.get("window")
    if known is not None:
        try:
            known.objectName()         # cheap probe for a dead wrapper
            return known
        except RuntimeError:
            _state["window"] = None    # deleted underneath us
    # Never told, or told about a window that has since been destroyed.
    # The tool that calls this is itself being run from a menu on the
    # window we want, so asking Qt is reliable here in a way it would
    # not be at startup.
    for getter in (QApplication.activeWindow, QApplication.activeModalWidget):
        try:
            found = getter()
        except (AttributeError, RuntimeError):
            continue
        if found is not None:
            return found.window()
    return None


def attached(window=None) -> bool:
    """Is the hook on the mod list right now?"""
    window = _window(window)
    if window is None:
        return False
    try:
        view = find_view(window)
    except RuntimeError:
        return False
    return view is not None and bool(view.property(HOOKED))


def reattach(window=None) -> tuple:
    """Put the entries back. Returns (ok, a sentence saying what happened).

    Exists because this hook reaches into MO2's own widgets, and anything
    that can come off should have a way back on that does not involve
    restarting. The timer usually gets there first; this is for when it
    has not, and for finding out which of the several things that could
    be wrong actually is.
    """
    window = _window(window)
    build = _state.get("build")
    if window is None or build is None:
        return False, ("The sorter has not finished starting up yet. Open a "
                       "mod list and try again, or restart MO2.")
    try:
        view = find_view(window)
    except RuntimeError:
        return False, "MO2's window has gone away. Restart MO2."
    if view is None:
        return False, ("MO2's mod list could not be found in this window, so "
                       "there is nothing to attach to. This usually means a "
                       "newer MO2 renamed or restructured it. Both actions "
                       "are still on the Tools menu.")
    if view.property(HOOKED):
        # Forget the flag and hook again: the flag can outlive the filter
        # if MO2 tore the filter down without replacing the widget, and
        # "already attached" is the least useful answer to a complaint
        # that it is not working.
        view.setProperty(HOOKED, False)
    hook = install(window, build)
    if hook is None:
        return False, "The mod list refused the hook. Restart MO2."
    return True, ("Attached. Right-click one or more mods in the left pane "
                  "and the sorter's entries will be at the bottom of the "
                  "menu.")
