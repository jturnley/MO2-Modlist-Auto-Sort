"""Component A: the MO2 plugin interface.

Kept deliberately thin. It collects paths from the organizer, runs the
pipeline, shows the user what would change, and only writes when they say so -
a sort that silently rewrote a thousand-line mod list would be very hard to
argue with after the fact.
"""

from __future__ import annotations

import json
import os

import mobase
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import (backups, category_ui, context_menu, dag,
               decisions as decisions_mod,
               incremental, modlist, nexus, nexus_bridge, pipeline,
               key_ui, launchcheck, loadcheck, nexus_api, resolve_ui,
               restore_ui, scan, skse, slots as slots_mod, stacks, vault_key)
from . import _i18n, install_action, install_ui
from ._i18n import QT_TRANSLATE_NOOP, translate

import contextlib
import time


@contextlib.contextmanager
def busy():
    """A wait cursor while the sorter works, so MO2 does not look hung.

    Taken down before any dialog appears - callers wrap only the work.
    """
    try:
        from PyQt6.QtCore import Qt
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        shown = True
    except Exception:
        shown = False
    try:
        yield
    finally:
        if shown:
            QApplication.restoreOverrideCursor()

VERSION = mobase.VersionInfo(0, 11, 0, mobase.ReleaseType.BETA)
MAX_LISTED = 30
BREAK = chr(10)

# Every string here is translated in the "DagSorter" context, written out at
# each call so pylupdate6 can find it. (self.tr would file the strings under
# each class's own name, and they would never be found at run time.)

# The kinds winners.py reports, as shown in the details list. The keys are
# what winners.py matches on, so only the label is translated.
KINDS = {
    "plugins": QT_TRANSLATE_NOOP("DagSorter", "plugins"),
    "archives": QT_TRANSLATE_NOOP("DagSorter", "archives"),
    "SKSE plugins": QT_TRANSLATE_NOOP("DagSorter", "SKSE plugins"),
    "scripts": QT_TRANSLATE_NOOP("DagSorter", "scripts"),
}


class DagSorterTool(mobase.IPluginTool):
    def __init__(self) -> None:
        mobase.IPluginTool.__init__(self)
        self._organizer = None

    # -- identity --------------------------------------------------------
    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        try:
            _i18n.install(organizer)
        except Exception:
            pass                # untranslated is fine; not loading is not
        # A load order is built one mod at a time, so the questions the
        # sorter cannot answer are best asked one mod at a time too. This is
        # the whole difference between two questions after an install and
        # thirty-three in one sitting months later.
        # Always hooked: whether anything happens is the on_install setting,
        # read at the moment of each install, so changing it from the Tools
        # menu takes effect without a restart.
        try:
            organizer.modList().onModInstalled(self._on_installed)
        except (AttributeError, RuntimeError, TypeError):
            pass                       # an MO2 build without the callback
        # The file lists kept between sorts (scan.SESSION) are dropped when
        # something may have changed what is in the mod folders: a mod
        # removed, or any program run through MO2 - BodySlide, Nemesis and
        # DynDOLOD all write into mod folders.
        try:
            organizer.modList().onModRemoved(self._on_removed)
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            organizer.onFinishedRun(self._on_finished_run)
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            organizer.onUserInterfaceInitialized(self._ui_ready)
        except (AttributeError, RuntimeError, TypeError):
            pass
        # Check SKSE plugins at the two moments it costs something to get
        # wrong: when the game is about to start, and when the mod that
        # brought a plugin has just been installed.
        try:
            skse.load_cache(self._skse_cache())
            organizer.onAboutToRun(self._skse_before_launch)
            organizer.modList().onModInstalled(self._skse_on_install)
        except (AttributeError, RuntimeError, TypeError):
            pass
        # Record the menu builder now, not in _ui_ready. If the callback
        # above never fires - an older MO2, or a launch where the hook
        # went in and later came off - the repair entry still has
        # something to put back, and can say what is missing instead of
        # only that something is.
        try:
            context_menu.remember(None, self._build_menu)
        except AttributeError:
            pass
        return True

    # -- the left pane right-click menu -----------------------------------
    def _ui_ready(self, window) -> None:
        # Held on the plugin so the timer is not collected, and so a second
        # call (MO2 has been known to fire this more than once) replaces the
        # keeper rather than running two of them.
        old = getattr(self, "_menu_keeper", None)
        if old is not None:
            try:
                old.stop()
            except RuntimeError:
                pass
        try:
            self._menu_keeper = context_menu.keep(window, self._build_menu)
        except Exception:
            # The right-click entries are a convenience; both actions are
            # in the Tools menu regardless, so this never stops MO2 loading.
            self._menu_keeper = None

    def _build_menu(self, names, menu) -> None:
        """Append our entries to the menu MO2 has just built."""
        from PyQt6.QtGui import QAction
        nodes, _header = modlist.read_modlist(self._list_path())
        rules = decisions_mod.Decisions(os.path.join(
            self._organizer.pluginDataPath(), "user_rules.json"))

        menu.addSeparator()
        label = (translate("DagSorter", "Auto-sort this mod")
                 if len(names) == 1 else
                 translate("DagSorter", "Auto-sort these %n mods", None,
                           len(names)))
        action = QAction(label, menu)
        action.triggered.connect(
            lambda _checked=False, picked=list(names): self._sort_these(picked))
        menu.addAction(action)

        # A selection freezes as one stack.  "The mod below it" means
        # something different for each member, but "below the whole
        # selection" does not, so the target is read off the edge of the
        # selection and the members are chained to each other in pane order.
        picked = stacks.in_pane_order(nodes, names)
        if not picked:
            return
        for side in ("above", "below"):
            target = stacks.stack_target(nodes, picked, side)
            if not target:
                continue
            # Both sides stay on the menu even when one is already set: the
            # point of offering the opposite is to be able to change one's
            # mind, and a hidden option cannot be changed.
            # One whole sentence per side: "above"/"below" dropped into a
            # template cannot be translated, and word order differs.
            if len(picked) == 1:
                label = (translate("DagSorter",
                                   "Freeze order - keep directly above {}")
                         if side == "above" else
                         translate("DagSorter",
                                   "Freeze order - keep directly below {}")
                         ).format(target)
            elif side == "above":
                label = translate(
                    "DagSorter", "Freeze order - keep these %n mods "
                    "together, directly above {}", None,
                    len(picked)).format(target)
            else:
                label = translate(
                    "DagSorter", "Freeze order - keep these %n mods "
                    "together, directly below {}", None,
                    len(picked)).format(target)
            done = stacks.already_set(rules, picked, target, side)
            if done:
                label = translate("DagSorter", "{}   (already set)").format(
                    label)
            action = QAction(label, menu)
            action.setEnabled(not done)
            action.triggered.connect(
                lambda _checked=False, mods=list(picked), to=target, how=side:
                self._freeze_stack(mods, to, how))
            menu.addAction(action)

        held = [m for m in picked if rules.frozen_to(m)]
        if held:
            if len(picked) == 1:
                at, side = rules.frozen_to(picked[0])
                label = (translate("DagSorter", "Unfreeze from {} (above)")
                         if side == "above" else
                         translate("DagSorter", "Unfreeze from {} (below)")
                         ).format(at)
            else:
                label = translate("DagSorter", "Unfreeze %n of these {} mods",
                                  None, len(held)).format(len(picked))
            action = QAction(label, menu)
            action.triggered.connect(
                lambda _checked=False, mods=list(held): self._unfreeze(mods))
            menu.addAction(action)

    def name(self) -> str:
        return "MO2 DAG Sorter"

    def author(self) -> str:
        return "MO2-DAG-Sorter"

    def description(self) -> str:
        return translate("DagSorter", "Sorts the left pane by functional tier and override "
                       "conflicts, using a topological sort.")

    def version(self) -> mobase.VersionInfo:
        return VERSION

    def requirements(self):
        return []

    def settings(self):
        return [
            mobase.PluginSetting(
                "nexus_api_key",
                translate("DagSorter", "Optional. Leave blank: MO2's own Nexus key is used, "
                        "so there is no need to enter a second copy of it. "
                        "Set one only to bypass MO2's connection - and set "
                        "it from Tools -> Nexus API Key rather than here, "
                        "which can test it before you rely on it."), ""),
            mobase.PluginSetting(
                install_action.SETTING,
                translate("DagSorter", "What to do when a mod is installed: "
                          "ask, nothing, enable or enable_sort. Easier to "
                          "change from Tools -> New Mod Options."),
                install_action.DEFAULT),
            mobase.PluginSetting(
                "game_domain",
                translate("DagSorter", "Nexus game domain used for category lookups."),
                "skyrimspecialedition"),
            mobase.PluginSetting(
                "check_skse_plugins",
                translate("DagSorter", "Before SKSE or the game is launched, and after a "
                        "mod is installed, check that its SKSE plugins will "
                        "load - game version, SKSE version, Address Library "
                        "- and warn if not."), True),
            mobase.PluginSetting(
                "skse_check_ignored",
                translate("DagSorter", "SKSE problems you chose not to be warned about "
                        "again. Clear it to be warned about them again."),
                ""),
        ]

    def displayName(self) -> str:
        return translate("DagSorter", "Auto-Sort Left Pane")

    def tooltip(self) -> str:
        return translate("DagSorter", "Reorder the left pane with a topological sort")

    def icon(self):
        from PyQt6.QtGui import QIcon
        return QIcon()

    def _nexus(self):
        """The shared Nexus client, built once per session.

        Reused so the response cache is not reopened on every menu click,
        and so a whole batch of freezes costs one connection.
        """
        if not hasattr(self, "_nexus_client"):
            self._nexus_client = nexus_api.connect(self._organizer)
        return self._nexus_client

    # -- the run ---------------------------------------------------------
    def display(self) -> None:
        parent = QApplication.activeWindow()
        root = self._instance_root()
        profile = self._organizer.profile().name()

        domain = str(self._organizer.pluginSetting(
            self.name(), "game_domain") or "skyrimspecialedition")
        override_key = str(self._organizer.pluginSetting(
            self.name(), "nexus_api_key") or "").strip()
        if not override_key:
            # The vault holds one key for every plugin, sealed, so the
            # ini does not have to hold a plain-text copy of it.
            override_key = vault_key.key(self._organizer)
        cache_dir = self._organizer.pluginDataPath()

        # MO2 holds a Nexus key already and will not hand it to a plugin, so
        # the bridge borrows the connection rather than the credential. The
        # setting stays as an override for anyone who wants their own.
        resolver = None
        if not override_key:
            cache = nexus.NexusCache(
                os.path.join(cache_dir, "nexus_cache.json"))
            if not cache.category_names:
                cache.category_names = nexus.read_local_categories(root)
                cache._dirty = bool(cache.category_names)
            try:
                resolver = nexus_bridge.BridgeResolver(
                    self._organizer, cache, domain)
            except (AttributeError, RuntimeError):
                resolver = None        # an MO2 build without the bridge

        rules = decisions_mod.Decisions(
            os.path.join(cache_dir, "user_rules.json"))
        # The full sort walks every mod folder afresh: it is the one run to
        # get everything right. The re-sorts below, after a dialog, reuse
        # what this one just read.
        try:
            with busy():
                result = pipeline.sort_profile(
                    root, profile, api_key=override_key or None,
                    domain=domain, cache_dir=cache_dir, resolver=resolver,
                    decisions=rules, client=self._nexus(),
                    game_dir=self._game_dir())
        except dag.CycleError as exc:
            self._show_cycle(parent, exc)
            return

        # A category is asked about before the conflicts, because it can
        # settle them: knowing a mod is an armour replacer rather than a
        # texture set moves it a whole tier, and some of the pairs the
        # sorter was about to ask about stop being close together at all.
        if result.uncategorised:
            dialog = category_ui.CategoryDialog(result.uncategorised, rules,
                                                parent)
            if (dialog.exec() == category_ui.CategoryDialog.DialogCode.Accepted
                    and rules.categories):
                try:
                    with busy():
                        result = pipeline.sort_profile(
                            root, profile, api_key=override_key or None,
                            domain=domain, cache_dir=cache_dir,
                            resolver=None, decisions=rules,
                            client=self._nexus(), game_dir=self._game_dir(),
                            reuse_scan=True)
                except dag.CycleError as exc:
                    self._show_cycle(parent, exc)
                    return

        # Ask about anything the sorter had to settle by rule, then sort
        # again with the answers in hand - a decision can change the order,
        # so the preview must be of the list the user actually gets.
        if result.unresolved:
            dialog = resolve_ui.ResolveDialog(result.unresolved, rules, parent,
                                             result.nodes, domain)
            if dialog.exec() != resolve_ui.ResolveDialog.DialogCode.Accepted:
                return
            if dialog.apply_to_decisions():
                try:
                    with busy():
                        result = pipeline.sort_profile(
                            root, profile, api_key=override_key or None,
                            domain=domain, cache_dir=cache_dir,
                            resolver=None, decisions=rules,
                            client=self._nexus(), game_dir=self._game_dir(),
                            reuse_scan=True)
                except dag.CycleError as exc:
                    self._show_cycle(parent, exc)
                    return

        if not result.changed:
            QMessageBox.information(
                parent, translate("DagSorter", "Auto-Sort Left Pane"),
                translate("DagSorter", "The list is already in order.\n\n") + result.report)
            return

        self._warn_duplicates(parent, result.duplicates)
        if not self._confirm(parent, result):
            return

        backup = pipeline.apply_result(root, profile, result)
        self._organizer.refresh()
        QMessageBox.information(
            parent, translate("DagSorter", "Auto-Sort Left Pane"),
            translate("DagSorter",
                      "Moved %n mods.\n\nPrevious list saved as:\n{}", None,
                      len(result.moved)).format(
                backup or translate("DagSorter", "(no backup written)")))

    # -- one mod at a time -----------------------------------------------
    def _on_installed(self, mod) -> None:
        """Enable and place a newly installed mod, as the user chose.

        MO2 installs a mod switched off at the bottom of the list. The
        on_install setting says what follows - nothing, switch it on, or
        switch it on and slot it into the existing order - and "ask", the
        default, offers those three with a box to make the answer stick.
        """
        try:
            name = mod.name()
            path = mod.absolutePath()
        except (AttributeError, RuntimeError):
            return
        scan.SESSION.forget(path)          # new files, whatever happens next
        parent = QApplication.activeWindow()
        action = install_action.normalise(self._organizer.pluginSetting(
            self.name(), install_action.SETTING))
        active = self._is_active(name)
        if action == install_action.ASK:
            action, remember = install_ui.ask(name, active, parent)
            if remember:
                self._organizer.setPluginSetting(
                    self.name(), install_action.SETTING, action)
        enable, sort = install_action.steps(action, active)
        if enable and not self._enable(name):
            return                         # MO2 would not switch it on
        if sort:
            self._place_installed(name, parent, switched_on=enable)

    def _on_removed(self, name) -> None:
        try:
            scan.SESSION.forget(os.path.join(
                self._organizer.modsPath(), str(name)))
        except (AttributeError, RuntimeError, TypeError):
            scan.SESSION.forget()

    def _on_finished_run(self, *_args) -> None:
        scan.SESSION.forget()

    def _is_active(self, name: str) -> bool:
        try:
            state = self._organizer.modList().state(name)
            try:
                return bool(state & mobase.ModState.ACTIVE)
            except TypeError:
                return bool(int(state) & int(mobase.ModState.ACTIVE))
        except (AttributeError, RuntimeError, TypeError):
            return False

    def _enable(self, name: str) -> bool:
        """Switch a mod on, and give MO2 a moment to write that down.

        MO2 writes modlist.txt a short while after a change rather than at
        once, and placing the mod means rewriting that file. Writing before
        MO2 has would race it: whichever wrote last would win, and MO2's
        copy has the mod in its old place. So this waits - up to a few
        seconds, keeping MO2 responsive - until the file shows the mod
        switched on. If it never does, the placement treats the mod as on
        regardless, so it is not switched back off.
        """
        try:
            self._organizer.modList().setActive(name, True)
        except (AttributeError, RuntimeError, TypeError):
            return False
        from PyQt6.QtCore import QEventLoop
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.AllEvents, 50)
            try:
                nodes, _header = modlist.read_modlist(self._list_path())
            except OSError:
                nodes = []
            if any(n.name == name and n.enabled for n in nodes):
                break
            time.sleep(0.05)
        return True

    def _place_installed(self, name: str, parent, switched_on=False) -> None:
        """Slot one mod into the existing order, asking only about it."""
        root = self._instance_root()
        profile = self._organizer.profile().name()
        domain = str(self._organizer.pluginSetting(
            self.name(), "game_domain") or "skyrimspecialedition")
        cache_dir = self._organizer.pluginDataPath()
        rules = decisions_mod.Decisions(
            os.path.join(cache_dir, "user_rules.json"))
        forced = [name] if switched_on else []

        try:
            with busy():
                result = pipeline.sort_profile(
                    root, profile, api_key=None, domain=domain,
                    cache_dir=cache_dir, resolver=None, decisions=rules,
                    client=self._nexus(), game_dir=self._game_dir(),
                    reuse_scan=True, enabled=forced)
        except (dag.CycleError, OSError):
            return                     # never block an install

        mine = incremental.questions_for(result.unresolved, name)
        if mine:
            dialog = resolve_ui.ResolveDialog(mine, rules, parent,
                                              result.nodes, domain)
            if dialog.exec() == resolve_ui.ResolveDialog.DialogCode.Accepted:
                if dialog.apply_to_decisions():
                    try:
                        with busy():
                            result = pipeline.sort_profile(
                                root, profile, api_key=None, domain=domain,
                                cache_dir=cache_dir, resolver=None,
                                decisions=rules, client=self._nexus(),
                                game_dir=self._game_dir(), reuse_scan=True,
                                enabled=forced)
                    except (dag.CycleError, OSError):
                        return

        # Only the groups the new mod is in: a duplicate it had nothing to
        # do with is not news, and saying so on every install would train
        # the user to click through the one that matters.
        self._warn_duplicates(
            parent, [g for g in result.duplicates if name in g.names])

        current, header = modlist.read_modlist(
            os.path.join(root, "profiles", profile, "modlist.txt"))
        if switched_on:
            # Written as on even if MO2 has not saved that yet: rewriting
            # the file with the mod off would switch it back off.
            for node in current:
                if node.name == name and node.prefix == "-":
                    node.prefix = "+"
        placed = incremental.place_one(current, result.nodes, name,
                                         result.edges)
        was = [n.name for n in current]
        if [n.name for n in placed] == was:
            return
        modlist.write_modlist(
            os.path.join(root, "profiles", profile, "modlist.txt"),
            placed, header, label="install-" + name)
        self._organizer.refresh()
        QMessageBox.information(
            parent, translate("DagSorter", "Auto-Sort Left Pane"),
            translate("DagSorter", "Placed {} at position {} of {}.").format(
                name, [n.name for n in placed].index(name) + 1, len(placed)))


    # -- what the context menu does ---------------------------------------
    def _list_path(self) -> str:
        return os.path.join(self._instance_root(), "profiles",
                            self._organizer.profile().name(), "modlist.txt")

    def _rules(self):
        return decisions_mod.Decisions(os.path.join(
            self._organizer.pluginDataPath(), "user_rules.json"))

    def _freeze_stack(self, picked, target: str, side: str) -> None:
        """Hold a selection together, directly above or below one other mod.

        Everything is checked before anything is written.  A half-applied
        stack is worse than none: it would leave the list held in an
        arrangement nobody asked for, with no single thing to undo.
        """
        parent = QApplication.activeWindow()
        rules = self._rules()
        pairs = stacks.stack_pairs(picked, target, side)

        looped = stacks.loops(rules, pairs)
        if looped:
            self._explain_loop(parent, rules, *looped)
            return

        state = self._sorted_state()
        if state is None:
            return
        order, edges = state
        blocked = [(mod, to, dag.freeze_blockers(order, edges, mod, to, how))
                   for mod, to, how in pairs]
        blocked = [b for b in blocked if b[2]]
        if blocked:
            self._explain_block(parent, side, target, blocked)
            return

        # Replacing a rule the user set earlier is never done silently: the
        # old one may be months old and the reason for it forgotten.
        replacing = [(mod, rules.frozen_to(mod)) for mod, to, how in pairs
                     if rules.frozen_to(mod)
                     and rules.frozen_to(mod) != (to, how)]
        if replacing and not self._confirm_replace(parent, replacing):
            return

        for mod, to, how in pairs:
            rules.freeze(mod, to, "frozen from the left pane", how)
        rules.save()

        if len(picked) == 1:
            body = (translate(
                "DagSorter",
                "{0} will be kept directly above {1}.\n\nAny other mods "
                "frozen to the same side of {1} sort among themselves as "
                "usual, and the whole stack stays there.")
                if side == "above" else translate(
                "DagSorter",
                "{0} will be kept directly below {1}.\n\nAny other mods "
                "frozen to the same side of {1} sort among themselves as "
                "usual, and the whole stack stays there.")
            ).format(picked[0], target)
        else:
            body = (translate(
                "DagSorter",
                "These %n mods will be kept together, directly above {0}, "
                "in the order they are in now:\n\n{1}\n\nThey move as one "
                "block. Sorting will not put anything between them.",
                None, len(picked))
                if side == "above" else translate(
                "DagSorter",
                "These %n mods will be kept together, directly below {0}, "
                "in the order they are in now:\n\n{1}\n\nThey move as one "
                "block. Sorting will not put anything between them.",
                None, len(picked))
            ).format(target, "\n".join("    " + m for m in picked))
        QMessageBox.information(parent, translate("DagSorter", "Freeze order"),
                                body)

    def _explain_loop(self, parent, rules, mod, target) -> None:
        chain = rules.freeze_chain(target)
        upto = []
        if chain and chain[0] != mod:
            upto = chain[1:chain.index(mod) + 1] if mod in chain else chain[1:]
        first = chain[0] if chain else mod
        if upto:
            text = translate(
                "DagSorter",
                "{0} cannot be frozen to {1}.\n\n{1} is already held next "
                "to {2}, which is held next to {3}, so this would form a "
                "loop: each mod would have to sit beside the other, and "
                "there is no arrangement that does both.\n\nUnfreeze one "
                "of them first. Nothing has been changed.").format(
                    mod, target, first, " -> ".join(upto))
        else:
            text = translate(
                "DagSorter",
                "{0} cannot be frozen to {1}.\n\n{1} is already held next "
                "to {2}, so this would form a loop: each mod would have to "
                "sit beside the other, and there is no arrangement that "
                "does both.\n\nUnfreeze one of them first. Nothing has been "
                "changed.").format(mod, target, first)
        QMessageBox.warning(parent, translate("DagSorter", "Freeze order"),
                            text)

    def _confirm_replace(self, parent, replacing) -> bool:
        if len(replacing) == 1:
            mod, (at, how) = replacing[0]
            text = (translate(
                "DagSorter",
                "{} is already frozen directly above {}.\n\nReplacing it "
                "will discard that earlier choice. A mod can only be held on "
                "one side of one mod.\n\nReplace it?")
                if how == "above" else translate(
                "DagSorter",
                "{} is already frozen directly below {}.\n\nReplacing it "
                "will discard that earlier choice. A mod can only be held on "
                "one side of one mod.\n\nReplace it?")).format(mod, at)
        else:
            text = translate(
                "DagSorter",
                "%n of the selected mods are already frozen somewhere "
                "else:\n\n{}\n\nGoing ahead will discard those earlier "
                "choices. A mod can only be held on one side of one "
                "mod.\n\nReplace them?", None, len(replacing)).format(
                    "\n".join(
                        (translate("DagSorter", "    {} - directly above {}")
                         if how == "above" else
                         translate("DagSorter", "    {} - directly below {}")
                         ).format(mod, at)
                        for mod, (at, how) in replacing))
        return QMessageBox.question(
            parent, translate("DagSorter", "Freeze order"), text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes

    def _sorted_state(self):
        """(sortable order, edges) for the list as it stands, or None.

        One sort serves a whole batch of freezes; on a list this size it is
        much the most expensive thing a menu click can do.
        """
        try:
            with busy():
                result = pipeline.sort_profile(
                self._instance_root(), self._organizer.profile().name(),
                reuse_scan=True,
                api_key=None,
                domain=str(self._organizer.pluginSetting(
                    self.name(), "game_domain") or "skyrimspecialedition"),
                cache_dir=self._organizer.pluginDataPath(),
                resolver=None, decisions=self._rules(), client=self._nexus(),
                game_dir=self._game_dir())
        except (dag.CycleError, OSError) as exc:
            QMessageBox.warning(QApplication.activeWindow(),
                                translate("DagSorter", "Freeze order"), str(exc))
            return None
        self._order = [n for n in result.nodes
                       if not n.is_separator and not n.is_unmanaged]
        return self._order, result.edges

    def _explain_block(self, parent, side, target, blocked) -> None:
        """Say what is in the way, in the words of the thing that put it there."""
        reasons = []
        for mod, _to, edges in blocked:
            reasons.extend(dag.blocker_text(self._order, e, mod)
                           for e in edges)
        if len(blocked) == 1:
            headline = (translate("DagSorter",
                                  "{} cannot be kept directly above {}.")
                        if side == "above" else
                        translate("DagSorter",
                                  "{} cannot be kept directly below {}.")
                        ).format(blocked[0][0], target)
        else:
            headline = (translate(
                "DagSorter", "%n of the selected mods cannot be kept "
                "directly above {}.", None, len(blocked))
                if side == "above" else translate(
                "DagSorter", "%n of the selected mods cannot be kept "
                "directly below {}.", None, len(blocked))).format(target)
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(translate("DagSorter", "Freeze order"))
        box.setText(headline)
        box.setInformativeText(translate("DagSorter", 
            "Something has to load in between, and moving them there would "
            "break it. Nothing has been changed.\n\n{}").format(
                "\n\n".join(reasons[:3])))
        if len(reasons) > 3:
            box.setDetailedText("\n\n".join(reasons))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _unfreeze(self, mods) -> None:
        rules = self._rules()
        was = [(m, rules.frozen_to(m)) for m in mods if rules.frozen_to(m)]
        for mod, _held in was:
            rules.unfreeze(mod)
        rules.save()
        if len(was) == 1:
            mod, (at, how) = was[0]
            text = (translate("DagSorter", "{} is no longer held above {}.")
                    if how == "above" else
                    translate("DagSorter", "{} is no longer held below {}.")
                    ).format(mod, at)
        else:
            text = translate("DagSorter",
                             "%n mods are no longer held in place:\n\n{}",
                             None, len(was)).format(
                                 "\n".join("    " + m for m, _h in was))
        QMessageBox.information(QApplication.activeWindow(),
                                translate("DagSorter", "Freeze order"), text)

    def _sort_these(self, names) -> None:
        """Place the selected mods, leaving the rest of the list alone."""
        parent = QApplication.activeWindow()
        root = self._instance_root()
        profile = self._organizer.profile().name()
        domain = str(self._organizer.pluginSetting(
            self.name(), "game_domain") or "skyrimspecialedition")
        cache_dir = self._organizer.pluginDataPath()
        rules = self._rules()
        # The user pointed at these mods and asked about them, so their
        # Nexus data is re-fetched rather than served from cache - a
        # requirement added since the last sweep is exactly the thing
        # they are likely to be chasing. Everything else in the graph
        # still comes from cache: the whole list has to be walked to
        # place one mod correctly, and re-asking about all of it would
        # spend hundreds of requests to answer a question about three.
        client = self._nexus()
        try:
            # Only the selected mods' folders are re-read: the user
            # pointed at them, and the rest has not changed since.
            for picked in names:
                scan.SESSION.forget(os.path.join(root, "mods", picked))
            with busy(), nexus_api.refreshing(client):
                result = pipeline.sort_profile(
                    root, profile, api_key=None, domain=domain,
                    cache_dir=cache_dir, resolver=None, decisions=rules,
                    client=client, refresh=names,
                    game_dir=self._game_dir(), reuse_scan=True)
        except (dag.CycleError, OSError) as exc:
            QMessageBox.warning(parent, translate("DagSorter", "Auto-sort"), str(exc))
            return

        asked = [edge for name in names
                 for edge in incremental.questions_for(result.unresolved, name)]
        if asked:
            dialog = resolve_ui.ResolveDialog(asked, rules, parent,
                                              result.nodes, domain)
            if dialog.exec() == resolve_ui.ResolveDialog.DialogCode.Accepted:
                if dialog.apply_to_decisions():
                    try:
                        # No refresh this time: the run above already
                        # fetched these, and the answers are now in cache.
                        with busy():
                            result = pipeline.sort_profile(
                                root, profile, api_key=None, domain=domain,
                                cache_dir=cache_dir, resolver=None,
                                decisions=rules, client=client,
                                game_dir=self._game_dir(), reuse_scan=True)
                    except (dag.CycleError, OSError):
                        return

        path = self._list_path()
        current, header = modlist.read_modlist(path)
        before = [n.name for n in current]
        for name in names:
            current = incremental.place_one(current, result.nodes, name,
                                            result.edges)
        after = [n.name for n in current]
        if after == before:
            QMessageBox.information(parent, translate("DagSorter", "Auto-sort"),
                                    translate("DagSorter", "Already in the right place."))
            return
        modlist.write_modlist(path, current, header,
                              label="auto-sort-" + names[0])
        self._organizer.refresh()
        QMessageBox.information(
            parent, translate("DagSorter", "Auto-sort"),
            translate("DagSorter", "Placed {}.\n\n{}").format(
                ", ".join(names),
                "\n".join("{}: {} -> {}".format(n, before.index(n) + 1,
                                                 after.index(n) + 1)
                           for n in names)))

    # -- dialogs ---------------------------------------------------------
    def _warn_duplicates(self, parent, found) -> None:
        """Say that two mods carry the same files, and leave it at that.

        Shown whatever the user decides about the sort, because it is not a
        statement about order: two mods shipping an identical manifest are
        the same content installed twice however the list is arranged.
        """
        if not found:
            return
        lines = []
        for group in found:
            lines.append(group.headline)
            lines.extend("    " + path for path in group.sample)
            if group.files > len(group.sample):
                lines.append(translate("DagSorter", "    ... and %n more", None,
                                       group.files - len(group.sample)))
            lines.append("")
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(translate("DagSorter", "Duplicate mods"))
        box.setText(translate("DagSorter", 
            "These mods replicate exactly the same files. You may want to "
            "check them to see if they are intended to be this way."))
        box.setInformativeText("\n".join(g.headline for g in found))
        box.setDetailedText("\n".join(lines).strip())
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _confirm(self, parent, result) -> bool:
        lines = ["{} -> {}   {}".format(old, new, name)
                 for name, old, new in result.moved[:MAX_LISTED]]
        if len(result.moved) > MAX_LISTED:
            lines.append(translate("DagSorter", "... and %n more", None,
                                   len(result.moved) - MAX_LISTED))
        if result.unfrozen:
            lines.append("")
            lines.append(translate("DagSorter",
                                   "FREEZES NOT HONOURED - a real dependency "
                                   "came first:"))
            lines += [translate("DagSorter",
                                "  {} could not be kept directly above {}"
                                ).format(a, b)
                      for a, b in result.unfrozen]
        if result.shadowed:
            lines.append("")
            lines.append(translate("DagSorter",
                                   "FULLY OVERRIDDEN - these contribute "
                                   "nothing in the new order:"))
            lines += ["  " + item.headline for item in result.shadowed]
        change = getattr(result, "slots", None)
        if change is not None and change.became_full:
            lines.append("")
            lines.append(translate(
                "DagSorter", "NOW FULL-SIZE - the winning copy of these "
                "plugins is no longer ESL-flagged ({} -> {} full slots):"
            ).format(change.before, change.after))
            lines += [translate("DagSorter",
                                "  {} - now the copy from {}, was from {}"
                                ).format(p, loadcheck.shown(now),
                                         loadcheck.shown(was))
                      for p, now, was in change.became_full]
        handed = getattr(result, "winners", None)
        if handed:
            lines.append("")
            lines.append(translate("DagSorter",
                                   "CHANGES HANDS - a different mod's copy "
                                   "of these files wins in the new order:"))
            for kind in ("plugins", "archives", "SKSE plugins", "scripts"):
                changes = handed.of(kind)
                if not changes:
                    continue
                lines.append("  {} ({}):".format(
                    translate("DagSorter", KINDS[kind]), len(changes)))
                lines += [translate("DagSorter", "    {} - now {}, was {}"
                                    ).format(path, now, was)
                          for _k, path, now, was in changes[:MAX_LISTED]]
                if len(changes) > MAX_LISTED:
                    lines.append(translate("DagSorter", "    ... and %n more",
                                           None, len(changes) - MAX_LISTED))
        if result.problems:
            lines.append("")
            lines.append(translate("DagSorter",
                                   "WILL NOT LOAD, whatever the order - "
                                   "reported, nothing changed:"))
            lines += ["  " + item.headline for item in result.problems]
        if result.dropped:
            lines.append("")
            lines.append(translate("DagSorter", "Conflicts settled (%n):",
                                   None, len(result.dropped)))
            lines += ["  {} -> {}  ({})".format(e.parent, e.child, e.reason)
                      for e in result.dropped[:MAX_LISTED]]
        box = QMessageBox(parent)
        box.setWindowTitle(translate("DagSorter", "Auto-Sort Left Pane"))
        box.setText(translate("DagSorter", "%n of {} entries would move.",
                              None, len(result.moved)).format(
                                  len(result.nodes)))
        summary = result.report
        if result.shadowed:
            summary += "\n\n" + translate(
                "DagSorter", "%n mod(s) end up fully overridden and "
                "contribute nothing - see Details.", None,
                len(result.shadowed))
        if result.problems:
            summary += "\n\n" + translate(
                "DagSorter", "%n plugin(s), archive(s) or SKSE DLL(s) will "
                "not load at all, whatever the order - see Details. Nothing "
                "has been changed about them.", None, len(result.problems))
        if handed:
            summary += "\n\n" + translate("DagSorter", "{} - see Details."
                                          ).format(handed.headline[0].upper()
                                                   + handed.headline[1:])
        if change is not None and change.changed:
            summary += "\n\n" + translate(
                "DagSorter", "Full plugin slots: {} -> {} (limit {})."
            ).format(change.before, change.after, slots_mod.FULL_LIMIT)
            if change.over:
                box.setIcon(QMessageBox.Icon.Warning)
                summary += " " + translate(
                    "DagSorter", "The new order goes over the limit and the "
                    "game will not start with it - see Details for the "
                    "plugins that changed.")
        box.setInformativeText(summary)
        box.setDetailedText("\n".join(lines))
        box.setStandardButtons(QMessageBox.StandardButton.Apply
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Apply

    def _show_cycle(self, parent, exc) -> None:
        detail = ["{}  ->  {}   ({}, {})".format(
            e.parent, e.child, e.reason, e.detail) for e in exc.edges[:60]]
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(translate("DagSorter", "Auto-Sort Left Pane"))
        box.setText(translate("DagSorter", "Nothing was written: %n mods are "
                              "in a circular dependency.", None,
                              len(exc.nodes)))
        box.setInformativeText(", ".join(exc.nodes[:12]))
        box.setDetailedText("\n".join(detail))
        box.exec()

    def _instance_root(self) -> str:
        """The instance folder - one level above overwrite/."""
        return os.path.dirname(os.path.normpath(self._organizer.overwritePath()))

    # -- SKSE plugin check -------------------------------------------------
    def _skse_cache(self) -> str:
        return os.path.join(self._organizer.pluginDataPath(),
                            "skse_dll_cache.json")

    def _skse_game_dir(self):
        return (self._game_dir()
                or loadcheck.game_directory(self._instance_root()))

    def _enabled_mod_folders(self) -> list:
        """[(name, folder)] of the enabled mods, lowest priority first.

        Read from MO2 rather than modlist.txt, which MO2 writes a moment
        after a change and which can be behind at the instant of a launch.
        """
        mods = []
        mod_list = self._organizer.modList()
        for name in mod_list.allModsByProfilePriority():
            try:
                state = mod_list.state(name)
                try:
                    active = bool(state & mobase.ModState.ACTIVE)
                except TypeError:
                    active = bool(int(state) & int(mobase.ModState.ACTIVE))
                if not active:
                    continue
                mod = mod_list.getMod(name)
                if mod is None or mod.isSeparator() or mod.isForeign():
                    continue
                mods.append((name, mod.absolutePath()))
            except (AttributeError, RuntimeError, TypeError):
                continue
        return mods

    def _active_plugins(self):
        """The ticked plugins, lowercased, or None if MO2 will not say."""
        try:
            plugin_list = self._organizer.pluginList()
            return {name.lower() for name in plugin_list.pluginNames()
                    if plugin_list.state(name) == mobase.PluginState.ACTIVE}
        except (AttributeError, RuntimeError, TypeError):
            return None

    def _skse_ignored(self) -> set:
        raw = self._organizer.pluginSetting(self.name(), "skse_check_ignored")
        try:
            return set(json.loads(raw)) if raw else set()
        except (TypeError, ValueError):
            return set()

    def _skse_ignore(self, problems) -> None:
        keep = self._skse_ignored() | {launchcheck.fingerprint(p)
                                       for p in problems}
        self._organizer.setPluginSetting(self.name(), "skse_check_ignored",
                                         json.dumps(sorted(keep)))

    def _skse_before_launch(self, path, *_rest) -> bool:
        """MO2's about-to-run hook. True lets the program start.

        Accepts both of MO2's signatures - (path) and (path, folder, args).
        Any failure of the check itself lets the launch go ahead: this is a
        warning, and a broken warning must never stand between the user
        and their game.
        """
        try:
            if not self._organizer.pluginSetting(self.name(),
                                                 "check_skse_plugins"):
                return True
            if not launchcheck.is_launch(path):
                return True
            problems = launchcheck.check(self._enabled_mod_folders(),
                                         self._skse_game_dir(),
                                         self._organizer.overwritePath(),
                                         self._active_plugins())
            skse.save_cache(self._skse_cache())
            ignored = self._skse_ignored()
            problems = [p for p in problems
                        if launchcheck.fingerprint(p) not in ignored]
        except Exception:
            return True
        if not problems:
            return True
        return self._warn_before_launch(problems)

    def _warn_before_launch(self, problems) -> bool:
        from PyQt6.QtWidgets import QCheckBox
        box = QMessageBox(QApplication.activeWindow())
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(translate("DagSorter", "SKSE plugin check"))
        box.setText(translate("DagSorter",
                              "SKSE will not load %n of the things below.",
                              None, len(problems)) if len(problems) > 1 else
                    translate("DagSorter",
                              "SKSE will not load the plugin below."))
        shown = [p.headline for p in problems[:3]]
        if len(problems) > 3:
            shown.append(translate("DagSorter",
                                   "... and %n more - see Details.", None,
                                   len(problems) - 3))
        box.setInformativeText("\n\n".join(shown) + "\n\n" + translate("DagSorter", 
            "The game will start without them. Nothing has been changed."))
        box.setDetailedText("\n".join(p.headline for p in problems))
        launch = box.addButton(translate("DagSorter", "Launch anyway"),
                               QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(translate("DagSorter", "Cancel launch"),
                               QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        remember = QCheckBox(translate("DagSorter", "Don't warn me about these again"))
        box.setCheckBox(remember)
        box.exec()
        if remember.isChecked():
            self._skse_ignore(problems)
        return box.clickedButton() is launch

    def _skse_on_install(self, mod) -> None:
        """After an install: can this mod's SKSE plugins load at all?"""
        try:
            if not self._organizer.pluginSetting(self.name(),
                                                 "check_skse_plugins"):
                return
            problems = launchcheck.check_mod(mod.name(), mod.absolutePath(),
                                             self._skse_game_dir())
            skse.save_cache(self._skse_cache())
        except Exception:
            return
        if not problems:
            return
        QMessageBox.warning(
            QApplication.activeWindow(), translate("DagSorter", "SKSE plugin check"),
            translate("DagSorter", "{} was installed, but SKSE will not load "
                      "its plugin(s):", None, len(problems)).format(
                          mod.name()) + "\n\n"
            + "\n\n".join(p.headline for p in problems) + "\n\n"
            + translate("DagSorter", "Check the mod's page for a version that matches your "
                      "game and SKSE."))

    def _game_dir(self):
        """The game folder MO2 is managing, or None to read it from the INI."""
        try:
            return self._organizer.managedGame().gameDirectory().absolutePath()
        except Exception:
            return None



class DagRestoreTool(mobase.IPluginTool):
    """Undo the last change to modlist.txt, or go back to any dated copy.

    A separate tool rather than a button inside the sorter, because the
    moment it is wanted is the moment the sorter has just done something
    unwelcome - it has to be reachable without running the sorter again.
    """

    def __init__(self) -> None:
        mobase.IPluginTool.__init__(self)
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        return True

    def name(self) -> str:
        return "MO2 DAG Sorter - Restore"

    def author(self) -> str:
        return "MO2-DAG-Sorter"

    def description(self) -> str:
        return translate("DagSorter", "Undo the last change to the mod list, or restore any "
                       "earlier dated backup of it.")

    def version(self) -> mobase.VersionInfo:
        return VERSION

    def requirements(self):
        return []

    def settings(self):
        return []

    def master(self) -> str:
        return "MO2 DAG Sorter"

    def displayName(self) -> str:
        return translate("DagSorter", "Undo / Restore Mod List")

    def tooltip(self) -> str:
        return translate("DagSorter", "Go back to a previous version of the left pane")

    def icon(self):
        from PyQt6.QtGui import QIcon
        return QIcon()

    def _list_path(self) -> str:
        root = os.path.dirname(os.path.normpath(
            self._organizer.overwritePath()))
        return os.path.join(root, "profiles",
                            self._organizer.profile().name(), "modlist.txt")

    def display(self) -> None:
        parent = QApplication.activeWindow()
        path = self._list_path()
        found = backups.available(path)
        if not found:
            QMessageBox.information(
                parent, translate("DagSorter", "Undo / Restore Mod List"),
                translate("DagSorter", "No backups yet. One is written every time the "
                        "sorter changes the list."))
            return

        current = sum(1 for n in modlist.read_modlist(path)[0])
        box = QMessageBox(parent)
        box.setWindowTitle(translate("DagSorter", "Undo / Restore Mod List"))
        box.setText(translate("DagSorter", "The list holds %n mods.", None,
                              current))
        box.setInformativeText(translate("DagSorter", 
            "The most recent backup is:\n{}\n\nUndo goes straight back to "
            "it. Choose Restore to pick an older one.").format(
                found[0].headline))
        undo = box.addButton(translate("DagSorter", "Undo last change"),
                             QMessageBox.ButtonRole.AcceptRole)
        pick = box.addButton(translate("DagSorter", "Restore..."),
                             QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()

        clicked = box.clickedButton()
        if clicked is undo:
            self._restore(parent, path, found[0])
        elif clicked is pick:
            dialog = restore_ui.RestoreDialog(found, current, parent)
            if dialog.exec() == restore_ui.RestoreDialog.DialogCode.Accepted                     and dialog.chosen is not None:
                self._restore(parent, path, dialog.chosen)

    def _restore(self, parent, path: str, backup) -> None:
        confirm = QMessageBox.question(
            parent, translate("DagSorter", "Undo / Restore Mod List"),
            translate("DagSorter", "Replace the current mod list with this one?\n\n{}\n\n"
                    "The list being replaced is saved first, so this can be "
                    "undone too.").format(backup.headline))
        if confirm != QMessageBox.StandardButton.Yes:
            return
        saved = backups.restore(path, backup.path)
        self._organizer.refresh()
        QMessageBox.information(
            parent, translate("DagSorter", "Undo / Restore Mod List"),
            translate("DagSorter", "Restored {}.\n\nThe list it replaced was saved as:\n{}")
            .format(backup.stamp, saved or translate("DagSorter", "(not saved)")))


class DagKeyTool(mobase.IPluginTool):
    """Enter, test or remove the Nexus key the sorter falls back to.

    The setting has always existed; what it lacked was a way to find it and
    any way to tell whether what was typed in worked. Both matter here,
    because the key is only ever reached for when MO2's own connection has
    already failed - which is exactly when a silent second failure is
    hardest to tell apart from the first.

    **This tool is not registered when the Nexus API Extender is
    installed** - see `create_plugins`.
    The Extender presents an entry by the same name, and two of those in
    one menu is worse than either alone: neither says which is which, and
    the one a user picks decides whether their key lands in an encrypted
    file or in plain text in ModOrganizer.ini. The Extender owns the key
    when it is there. This is the fallback for someone who skipped the
    requirement, so it steps aside rather than competing.
    """

    def __init__(self) -> None:
        mobase.IPluginTool.__init__(self)
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        return True

    def name(self) -> str:
        return "MO2 DAG Sorter - Nexus Key"

    def author(self) -> str:
        return "MO2-DAG-Sorter"

    def description(self) -> str:
        return translate("DagSorter", "Enter your own Nexus API key for the sorter to use "
                       "when MO2's own connection is not enough.")

    def version(self) -> mobase.VersionInfo:
        return VERSION

    def requirements(self):
        return []

    def settings(self):
        return []

    def master(self) -> str:
        return "MO2 DAG Sorter"

    def displayName(self) -> str:
        return translate("DagSorter", "Nexus API Key")

    def tooltip(self) -> str:
        return translate("DagSorter", "Enter or test your own Nexus API key")

    def icon(self):
        from PyQt6.QtGui import QIcon
        return QIcon()

    def _bridge_works(self, domain: str) -> bool:
        """Whether MO2 will lend the sorter its own Nexus connection."""
        root = os.path.dirname(os.path.normpath(
            self._organizer.overwritePath()))
        try:
            cache = nexus.NexusCache(os.path.join(
                self._organizer.pluginDataPath(), "nexus_cache.json"))
            if not cache.category_names:
                cache.category_names = nexus.read_local_categories(root)
            nexus_bridge.BridgeResolver(self._organizer, cache, domain)
        except (AttributeError, RuntimeError, OSError):
            return False
        return True

    def display(self) -> None:
        domain = str(self._organizer.pluginSetting(
            "MO2 DAG Sorter", "game_domain") or "skyrimspecialedition")
        key_ui.KeyDialog(self._organizer, domain,
                         self._bridge_works(domain),
                         QApplication.activeWindow()).exec()


class DagInstallTool(mobase.IPluginTool):
    """Change what happens to newly installed mods.

    The install pop-up offers to remember its answer; this is where that
    answer is changed afterwards, without digging through Settings ->
    Plugins for a free-text value.
    """

    def __init__(self) -> None:
        mobase.IPluginTool.__init__(self)
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        return True

    def name(self) -> str:
        return "MO2 DAG Sorter - New Mods"

    def author(self) -> str:
        return "MO2-DAG-Sorter"

    def description(self) -> str:
        return translate("DagSorter", "Choose whether newly installed mods "
                         "are enabled, and sorted, automatically.")

    def version(self) -> mobase.VersionInfo:
        return VERSION

    def requirements(self):
        return []

    def settings(self):
        return []

    def master(self) -> str:
        return "MO2 DAG Sorter"

    def displayName(self) -> str:
        return translate("DagSorter", "New Mod Options")

    def tooltip(self) -> str:
        return translate("DagSorter", "What to do with a mod once MO2 has "
                         "installed it")

    def icon(self):
        from PyQt6.QtGui import QIcon
        return QIcon()

    def display(self) -> None:
        current = self._organizer.pluginSetting("MO2 DAG Sorter",
                                                install_action.SETTING)
        dialog = install_ui.OptionsDialog(current, QApplication.activeWindow())
        if dialog.exec() == install_ui.OptionsDialog.DialogCode.Accepted:
            self._organizer.setPluginSetting(
                "MO2 DAG Sorter", install_action.SETTING, dialog.choice)


class DagMenuTool(mobase.IPluginTool):
    """Put the sorter's right-click entries back on the mod list.

    The entries are not a plugin API - MO2 builds that menu in C++ and
    offers no hook - so the sorter attaches itself to the widget. That
    attachment can be lost: MO2 rebuilds the mod list when the plugin
    set changes, and a reload of an unrelated plugin has been observed
    to take these entries with it. A watchdog notices and reattaches
    within a few seconds, but "wait and see" is a poor answer to someone
    who is looking at a menu that no longer has what they want, so this
    does it on demand and says which of the possible faults it found.
    """

    def __init__(self) -> None:
        mobase.IPluginTool.__init__(self)
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        return True

    def name(self) -> str:
        return "MO2 DAG Sorter - Menu"

    def author(self) -> str:
        return "MO2-DAG-Sorter"

    def description(self) -> str:
        return translate("DagSorter", "Restore the sorter's right-click entries on the mod "
                       "list if they have gone missing.")

    def version(self) -> mobase.VersionInfo:
        return VERSION

    def requirements(self):
        return []

    def settings(self):
        return []

    def master(self) -> str:
        return "MO2 DAG Sorter"

    def displayName(self) -> str:
        return translate("DagSorter", "Restore Right-Click Menu")

    def tooltip(self) -> str:
        return translate("DagSorter", "Put the sorter's entries back on the mod list's "
                       "right-click menu")

    def icon(self):
        from PyQt6.QtGui import QIcon
        return QIcon()

    def display(self) -> None:
        # _parentWidget first: MO2 hands tools the main window, which is
        # exactly what needs hooking, and it is right even when the active
        # window is a dialog this tool was opened from.
        window = None
        for source in (self._parentWidget, QApplication.activeWindow):
            try:
                window = source()
            except (AttributeError, RuntimeError):
                window = None
            if window is not None:
                break

        title = translate("DagSorter", "Restore Right-Click Menu")
        was = context_menu.attached(window)
        ok, detail = context_menu.reattach(window)

        if ok and was:
            body = translate("DagSorter", "The entries were already attached, and "
                           "have been reattached anyway.")
            body = body + BREAK + BREAK + detail
        elif ok:
            body = translate("DagSorter", "The entries were missing.") + " " + detail
        else:
            body = detail

        if ok:
            QMessageBox.information(window, title, body)
        else:
            QMessageBox.warning(window, title, body)


def create_plugins():
    """The tools MO2 should register, decided before it asks.

    `DagKeyTool` is left out when the Extender is installed, because the
    Extender presents an entry by the same name and two of those in one
    menu is worse than either alone.

    Deciding it here rather than in `DagKeyTool.init` is deliberate. A
    tool returning False from `init` is unregistered, but MO2 reports it
    as a plugin that failed to initialise and then logs the whole package
    as failed to load - which looks like a broken install to anyone who
    reads the log, on every launch, for a case that is working exactly as
    intended. Not offering the tool says the same thing quietly.

    A key already typed into this plugin's setting is not stranded by
    this: the Extender's dialog offers to take that copy over and blank
    it.
    """
    tools = [DagSorterTool(), DagRestoreTool(), DagInstallTool(),
             DagMenuTool()]
    if not nexus_api.installed():
        tools.append(DagKeyTool())
    return tools
