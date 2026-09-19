"""Component A: the MO2 plugin interface.

Kept deliberately thin. It collects paths from the organizer, runs the
pipeline, shows the user what would change, and only writes when they say so -
a sort that silently rewrote a thousand-line mod list would be very hard to
argue with after the fact.
"""

from __future__ import annotations

import os

import mobase
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import (backups, category_ui, context_menu, dag,
               decisions as decisions_mod,
               incremental, modlist, nexus, nexus_bridge, pipeline,
               key_ui, nexus_api, resolve_ui, restore_ui, stacks,
               vault_key)

VERSION = mobase.VersionInfo(0, 9, 0, mobase.ReleaseType.BETA)
MAX_LISTED = 30


class DagSorterTool(mobase.IPluginTool):
    def __init__(self) -> None:
        mobase.IPluginTool.__init__(self)
        self._organizer = None

    # -- identity --------------------------------------------------------
    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        # A load order is built one mod at a time, so the questions the
        # sorter cannot answer are best asked one mod at a time too. This is
        # the whole difference between two questions after an install and
        # thirty-three in one sitting months later.
        try:
            if organizer.pluginSetting(self.name(), "sort_on_install"):
                organizer.modList().onModInstalled(self._on_installed)
        except (AttributeError, RuntimeError, TypeError):
            pass                       # an MO2 build without the callback
        try:
            organizer.onUserInterfaceInitialized(self._ui_ready)
        except (AttributeError, RuntimeError, TypeError):
            pass
        return True

    # -- the left pane right-click menu -----------------------------------
    def _ui_ready(self, window) -> None:
        context_menu.install(window, self._build_menu)

    def _build_menu(self, names, menu) -> None:
        """Append our entries to the menu MO2 has just built."""
        from PyQt6.QtGui import QAction
        nodes, _header = modlist.read_modlist(self._list_path())
        rules = decisions_mod.Decisions(os.path.join(
            self._organizer.pluginDataPath(), "user_rules.json"))

        menu.addSeparator()
        label = ("Auto-sort this mod" if len(names) == 1
                 else "Auto-sort these {} mods".format(len(names)))
        action = QAction(self.tr(label), menu)
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
            if len(picked) == 1:
                label = "Freeze order - keep directly {} {}".format(
                    side, target)
            else:
                label = ("Freeze order - keep these {} mods together, "
                         "directly {} {}").format(len(picked), side, target)
            done = stacks.already_set(rules, picked, target, side)
            if done:
                label += "   (already set)"
            action = QAction(self.tr(label), menu)
            action.setEnabled(not done)
            action.triggered.connect(
                lambda _checked=False, mods=list(picked), to=target, how=side:
                self._freeze_stack(mods, to, how))
            menu.addAction(action)

        held = [m for m in picked if rules.frozen_to(m)]
        if held:
            if len(picked) == 1:
                at, side = rules.frozen_to(picked[0])
                label = "Unfreeze from {} ({})".format(at, side)
            else:
                label = "Unfreeze {} of these {} mods".format(
                    len(held), len(picked))
            action = QAction(self.tr(label), menu)
            action.triggered.connect(
                lambda _checked=False, mods=list(held): self._unfreeze(mods))
            menu.addAction(action)

    def name(self) -> str:
        return "MO2 DAG Sorter"

    def author(self) -> str:
        return "MO2-DAG-Sorter"

    def description(self) -> str:
        return self.tr("Sorts the left pane by functional tier and override "
                       "conflicts, using a topological sort.")

    def version(self) -> mobase.VersionInfo:
        return VERSION

    def requirements(self):
        return []

    def settings(self):
        return [
            mobase.PluginSetting(
                "nexus_api_key",
                self.tr("Optional. Leave blank: MO2's own Nexus key is used, "
                        "so there is no need to enter a second copy of it. "
                        "Set one only to bypass MO2's connection - and set "
                        "it from Tools -> Nexus API Key rather than here, "
                        "which can test it before you rely on it."), ""),
            mobase.PluginSetting(
                "sort_on_install",
                self.tr("Place each newly installed mod straight away, and "
                        "ask about its conflicts then rather than saving "
                        "them all up for a full sort."), True),
            mobase.PluginSetting(
                "game_domain",
                self.tr("Nexus game domain used for category lookups."),
                "skyrimspecialedition"),
        ]

    def displayName(self) -> str:
        return self.tr("Auto-Sort Left Pane")

    def tooltip(self) -> str:
        return self.tr("Reorder the left pane with a topological sort")

    def icon(self):
        from PyQt6.QtGui import QIcon
        return QIcon()

    def tr(self, text: str) -> str:
        return QCoreApplication.translate("DagSorter", text)

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
        try:
            result = pipeline.sort_profile(
                root, profile, api_key=override_key or None, domain=domain,
                cache_dir=cache_dir, resolver=resolver, decisions=rules, client=self._nexus())
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
                    result = pipeline.sort_profile(
                        root, profile, api_key=override_key or None,
                        domain=domain, cache_dir=cache_dir,
                        resolver=None, decisions=rules, client=self._nexus())
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
                    result = pipeline.sort_profile(
                        root, profile, api_key=override_key or None,
                        domain=domain, cache_dir=cache_dir,
                        resolver=None, decisions=rules, client=self._nexus())
                except dag.CycleError as exc:
                    self._show_cycle(parent, exc)
                    return

        if not result.changed:
            QMessageBox.information(
                parent, self.tr("Auto-Sort Left Pane"),
                self.tr("The list is already in order.\n\n") + result.report)
            return

        self._warn_duplicates(parent, result.duplicates)
        if not self._confirm(parent, result):
            return

        backup = pipeline.apply_result(root, profile, result)
        self._organizer.refresh()
        QMessageBox.information(
            parent, self.tr("Auto-Sort Left Pane"),
            self.tr("Moved {} mods.\n\nPrevious list saved as:\n{}").format(
                len(result.moved), backup or self.tr("(no backup written)")))

    # -- one mod at a time -----------------------------------------------
    def _on_installed(self, mod) -> None:
        """Slot the mod that was just installed into the existing order."""
        try:
            name = mod.name()
        except (AttributeError, RuntimeError):
            return
        parent = QApplication.activeWindow()
        root = self._instance_root()
        profile = self._organizer.profile().name()
        domain = str(self._organizer.pluginSetting(
            self.name(), "game_domain") or "skyrimspecialedition")
        cache_dir = self._organizer.pluginDataPath()
        rules = decisions_mod.Decisions(
            os.path.join(cache_dir, "user_rules.json"))

        try:
            result = pipeline.sort_profile(
                root, profile, api_key=None, domain=domain,
                cache_dir=cache_dir, resolver=None, decisions=rules, client=self._nexus())
        except (dag.CycleError, OSError):
            return                     # never block an install

        mine = incremental.questions_for(result.unresolved, name)
        if mine:
            dialog = resolve_ui.ResolveDialog(mine, rules, parent,
                                              result.nodes, domain)
            if dialog.exec() == resolve_ui.ResolveDialog.DialogCode.Accepted:
                if dialog.apply_to_decisions():
                    try:
                        result = pipeline.sort_profile(
                            root, profile, api_key=None, domain=domain,
                            cache_dir=cache_dir, resolver=None,
                            decisions=rules, client=self._nexus())
                    except (dag.CycleError, OSError):
                        return

        # Only the groups the new mod is in: a duplicate it had nothing to
        # do with is not news, and saying so on every install would train
        # the user to click through the one that matters.
        self._warn_duplicates(
            parent, [g for g in result.duplicates if name in g.names])

        current, header = modlist.read_modlist(
            os.path.join(root, "profiles", profile, "modlist.txt"))
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
            parent, self.tr("Auto-Sort Left Pane"),
            self.tr("Placed {} at position {} of {}.").format(
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
            body = self.tr(
                "{} will be kept directly {} {}.\n\nAny other mods "
                "frozen to the same side of {} sort among themselves as "
                "usual, and the whole stack stays there.").format(
                    picked[0], side, target, target)
        else:
            body = self.tr(
                "These {} mods will be kept together, directly {} {}, in "
                "the order they are in now:\n\n{}\n\nThey move as one "
                "block. Sorting will not put anything between them.").format(
                    len(picked), side, target,
                    "\n".join("    " + m for m in picked))
        QMessageBox.information(parent, self.tr("Freeze order"), body)

    def _explain_loop(self, parent, rules, mod, target) -> None:
        chain = rules.freeze_chain(target)
        rest = ""
        if chain and chain[0] != mod:
            upto = chain[1:chain.index(mod) + 1] if mod in chain else chain[1:]
            if upto:
                rest = ", which is held next to " + " then ".join(upto)
        QMessageBox.warning(
            parent, self.tr("Freeze order"),
            self.tr("{} cannot be frozen to {}.\n\n{} is already held next "
                    "to {}{}, so this would form a loop: each mod would have "
                    "to sit beside the other, and there is no arrangement "
                    "that does both.\n\nUnfreeze one of them first. Nothing "
                    "has been changed.").format(
                        mod, target, target, chain[0] if chain else mod, rest))

    def _confirm_replace(self, parent, replacing) -> bool:
        if len(replacing) == 1:
            mod, (at, how) = replacing[0]
            text = self.tr(
                "{} is already frozen directly {} {}.\n\nReplacing it will "
                "discard that earlier choice. A mod can only be held on one "
                "side of one mod.\n\nReplace it?").format(mod, how, at)
        else:
            text = self.tr(
                "{} of the selected mods are already frozen somewhere "
                "else:\n\n{}\n\nGoing ahead will discard those earlier "
                "choices. A mod can only be held on one side of one "
                "mod.\n\nReplace them?").format(
                    len(replacing),
                    "\n".join("    {} - directly {} {}".format(mod, how, at)
                               for mod, (at, how) in replacing))
        return QMessageBox.question(
            parent, self.tr("Freeze order"), text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes

    def _sorted_state(self):
        """(sortable order, edges) for the list as it stands, or None.

        One sort serves a whole batch of freezes; on a list this size it is
        much the most expensive thing a menu click can do.
        """
        try:
            result = pipeline.sort_profile(
                self._instance_root(), self._organizer.profile().name(),
                api_key=None,
                domain=str(self._organizer.pluginSetting(
                    self.name(), "game_domain") or "skyrimspecialedition"),
                cache_dir=self._organizer.pluginDataPath(),
                resolver=None, decisions=self._rules(), client=self._nexus())
        except (dag.CycleError, OSError) as exc:
            QMessageBox.warning(QApplication.activeWindow(),
                                self.tr("Freeze order"), str(exc))
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
        who = (blocked[0][0] if len(blocked) == 1
               else "{} of the selected mods".format(len(blocked)))
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Freeze order"))
        box.setText(self.tr("{} cannot be kept directly {} {}.").format(
            who, side, target))
        box.setInformativeText(self.tr(
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
            text = self.tr("{} is no longer held {} {}.").format(mod, how, at)
        else:
            text = self.tr("{} mods are no longer held in place:\n\n{}"
                           ).format(len(was),
                                    "\n".join("    " + m for m, _h in was))
        QMessageBox.information(QApplication.activeWindow(),
                                self.tr("Freeze order"), text)

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
            with nexus_api.refreshing(client):
                result = pipeline.sort_profile(
                    root, profile, api_key=None, domain=domain,
                    cache_dir=cache_dir, resolver=None, decisions=rules,
                    client=client, refresh=names)
        except (dag.CycleError, OSError) as exc:
            QMessageBox.warning(parent, self.tr("Auto-sort"), str(exc))
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
                        result = pipeline.sort_profile(
                            root, profile, api_key=None, domain=domain,
                            cache_dir=cache_dir, resolver=None,
                            decisions=rules, client=client)
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
            QMessageBox.information(parent, self.tr("Auto-sort"),
                                    self.tr("Already in the right place."))
            return
        modlist.write_modlist(path, current, header,
                              label="auto-sort-" + names[0])
        self._organizer.refresh()
        QMessageBox.information(
            parent, self.tr("Auto-sort"),
            self.tr("Placed {}.\n\n{}").format(
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
                lines.append("    ... and {} more".format(
                    group.files - len(group.sample)))
            lines.append("")
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(self.tr("Duplicate mods"))
        box.setText(self.tr(
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
            lines.append("... and {} more".format(
                len(result.moved) - MAX_LISTED))
        if result.unfrozen:
            lines.append("")
            lines.append("FREEZES NOT HONOURED - a real dependency came "
                         "first:")
            lines += ["  {} could not be kept directly above {}".format(a, b)
                      for a, b in result.unfrozen]
        if result.shadowed:
            lines.append("")
            lines.append("FULLY OVERRIDDEN - these contribute nothing in the "
                         "new order:")
            lines += ["  " + item.headline for item in result.shadowed]
        if result.dropped:
            lines.append("")
            lines.append("Conflicts settled ({}):".format(len(result.dropped)))
            lines += ["  {} -> {}  ({})".format(e.parent, e.child, e.reason)
                      for e in result.dropped[:MAX_LISTED]]
        box = QMessageBox(parent)
        box.setWindowTitle(self.tr("Auto-Sort Left Pane"))
        box.setText(self.tr("{} of {} entries would move.").format(
            len(result.moved), len(result.nodes)))
        summary = result.report
        if result.shadowed:
            summary += ("\n\n{} mod(s) end up fully overridden and "
                        "contribute nothing - see Details.").format(
                            len(result.shadowed))
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
        box.setWindowTitle(self.tr("Auto-Sort Left Pane"))
        box.setText(self.tr("Nothing was written: {} mods are in a circular "
                            "dependency.").format(len(exc.nodes)))
        box.setInformativeText(", ".join(exc.nodes[:12]))
        box.setDetailedText("\n".join(detail))
        box.exec()

    def _instance_root(self) -> str:
        """The instance folder - one level above overwrite/."""
        return os.path.dirname(os.path.normpath(self._organizer.overwritePath()))



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
        return self.tr("Undo the last change to the mod list, or restore any "
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
        return self.tr("Undo / Restore Mod List")

    def tooltip(self) -> str:
        return self.tr("Go back to a previous version of the left pane")

    def icon(self):
        from PyQt6.QtGui import QIcon
        return QIcon()

    def tr(self, text: str) -> str:
        return QCoreApplication.translate("DagSorter", text)

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
                parent, self.tr("Undo / Restore Mod List"),
                self.tr("No backups yet. One is written every time the "
                        "sorter changes the list."))
            return

        current = sum(1 for n in modlist.read_modlist(path)[0])
        box = QMessageBox(parent)
        box.setWindowTitle(self.tr("Undo / Restore Mod List"))
        box.setText(self.tr("The list holds {} mods.").format(current))
        box.setInformativeText(self.tr(
            "The most recent backup is:\n{}\n\nUndo goes straight back to "
            "it. Choose Restore to pick an older one.").format(
                found[0].headline))
        undo = box.addButton(self.tr("Undo last change"),
                             QMessageBox.ButtonRole.AcceptRole)
        pick = box.addButton(self.tr("Restore..."),
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
            parent, self.tr("Undo / Restore Mod List"),
            self.tr("Replace the current mod list with this one?\n\n{}\n\n"
                    "The list being replaced is saved first, so this can be "
                    "undone too.").format(backup.headline))
        if confirm != QMessageBox.StandardButton.Yes:
            return
        saved = backups.restore(path, backup.path)
        self._organizer.refresh()
        QMessageBox.information(
            parent, self.tr("Undo / Restore Mod List"),
            self.tr("Restored {}.\n\nThe list it replaced was saved as:\n{}")
            .format(backup.stamp, saved or self.tr("(not saved)")))


class DagKeyTool(mobase.IPluginTool):
    """Enter, test or remove the Nexus key the sorter falls back to.

    The setting has always existed; what it lacked was a way to find it and
    any way to tell whether what was typed in worked. Both matter here,
    because the key is only ever reached for when MO2's own connection has
    already failed - which is exactly when a silent second failure is
    hardest to tell apart from the first.
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
        return self.tr("Enter your own Nexus API key for the sorter to use "
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
        return self.tr("Nexus API Key")

    def tooltip(self) -> str:
        return self.tr("Enter or test your own Nexus API key")

    def icon(self):
        from PyQt6.QtGui import QIcon
        return QIcon()

    def tr(self, text: str) -> str:
        return QCoreApplication.translate("DagSorter", text)

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


def create_plugins():
    return [DagSorterTool(), DagRestoreTool(), DagKeyTool()]
