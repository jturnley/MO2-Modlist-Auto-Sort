"""Standing answers to the conflicts the sorter cannot settle on its own.

When two edges contradict each other one of them has to yield, and the
sorter's rule for choosing - a master outranks a tier, a bigger conflict
outranks a smaller one - is a reasonable default and nothing more.  The
person who installed the mods knows which way round they want it.

So every unresolved pair can be answered once and the answer kept.  A
decision is stored against the two mod names, not against an edge or a
priority, so it survives everything: reinstalls, new mods, a list sorted from
scratch.  It only stops applying if one of the two mods is uninstalled.

    {"pairs": [{"first": "Caesia Ostim Patch",
                "second": "Caesia Ostim - Sequenced",
                "note": "the patch is a master, keep it above"}],
     "pins":  [{"mod": "Crash Logger SSE AE VR - PDB support",
                "at": "top",
                "note": "must load first to catch everything else"}]}

"first" loads first, which in MO2's left pane means it sits higher and
**loses** the conflict to "second".

A freeze is the third: "keep this mod directly above that one", or
directly below it.  It says
nothing about why - the user saw the two sitting together and wants them to
stay that way - so it is applied after the sort rather than as an edge.  If
several mods are frozen to the same side of the same target they form a
stack: sorted among themselves by the ordinary rules, then moved as one
block to that side of their target.

A pin is the other kind of standing instruction: not a pair but a place.
Some mods have a right position that no amount of evidence about their
files can express - a crash logger has to be first because it must be
loaded before there is anything to crash, which is a fact about when it
runs, not about what it overrides.
"""

from __future__ import annotations

import json
import os


# How alike two names must be before a rule is moved from one to the other,
# and how far ahead of the runner-up the winner has to be. Both only apply
# when a Nexus id covers several installed mods, which is the common case:
# "At Your Own Pace - Companions" and "- Dawnguard" share an id and are not
# interchangeable. Deliberately strict - skipping is visible, moving a rule
# onto the wrong mod is not.
NAME_MATCH = 0.60
NAME_MARGIN = 0.15


def key(a: str, b: str) -> tuple[str, str]:
    """A pair's identity, independent of which way round it is asked."""
    return (a, b) if a <= b else (b, a)


class Decisions:
    def __init__(self, path: str) -> None:
        self.path = path
        # {pair key: name that loads first}
        self.first: dict[tuple[str, str], str] = {}
        self.notes: dict[tuple[str, str], str] = {}
        # {mod name: "top" or "bottom"}
        self.pins: dict[str, str] = {}
        self.pin_notes: dict[str, str] = {}
        # {mod name: the mod it must sit directly above}
        self.freezes: dict[str, str] = {}
        # {mod name: the mod it must sit directly below}
        self.freezes_below: dict[str, str] = {}
        self.freeze_notes: dict[str, str] = {}
        # {mod name: the category name the user gave it}
        self.categories: dict[str, str] = {}
        # {mod name: its Nexus mod id}, learned on each sort for every mod a
        # rule mentions. Every rule here is keyed by the name MO2 shows, and
        # that name is not stable: reinstalling or upgrading a mod can change
        # it, at which point a freeze the user set deliberately stops
        # matching anything and is silently ignored. The id does not change,
        # so it is what lets the rule be carried across to the new name.
        self.ids: dict[str, int] = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        for entry in raw.get("pairs") or ():
            try:
                a, b = str(entry["first"]), str(entry["second"])
            except (KeyError, TypeError):
                continue
            self.first[key(a, b)] = a
            note = entry.get("note")
            if note:
                self.notes[key(a, b)] = str(note)
        for entry in raw.get("pins") or ():
            try:
                mod = str(entry["mod"])
            except (KeyError, TypeError):
                continue
            where = str(entry.get("at", "top")).lower()
            if where not in ("top", "bottom"):
                continue
            self.pins[mod] = where
            if entry.get("note"):
                self.pin_notes[mod] = str(entry["note"])
        for mod, name in (raw.get("categories") or {}).items():
            if str(name).strip():
                self.categories[str(mod)] = str(name).strip()
        for mod, nexus in (raw.get("ids") or {}).items():
            try:
                if int(nexus) > 0:
                    self.ids[str(mod)] = int(nexus)
            except (TypeError, ValueError):
                continue
        for entry in raw.get("freezes") or ():
            try:
                mod = str(entry["mod"])
            except (KeyError, TypeError):
                continue
            if "above" in entry:
                target, where = str(entry["above"]), self.freezes
            elif "below" in entry:
                target, where = str(entry["below"]), self.freezes_below
            else:
                continue
            if mod == target:
                continue
            where[mod] = target
            if entry.get("note"):
                self.freeze_notes[mod] = str(entry["note"])

    # -- surviving a rename ----------------------------------------------

    def names(self) -> set:
        """Every mod name any rule here refers to."""
        found = set(self.pins) | set(self.freezes) | set(self.categories)
        found |= set(self.freezes_below) | set(self.freezes_below.values())
        found |= set(self.freezes.values())
        for a, b in self.first:
            found.add(a)
            found.add(b)
        return found

    def rename(self, old: str, new: str) -> None:
        """Move every rule mentioning `old` onto `new`."""
        if old == new or not new:
            return

        def swap(name):
            return new if name == old else name

        self.first = {key(swap(a), swap(b)): swap(v)
                      for (a, b), v in self.first.items()}
        self.notes = {key(swap(a), swap(b)): v
                      for (a, b), v in self.notes.items()}
        for store in (self.pins, self.pin_notes, self.categories):
            if old in store:
                store[swap(old)] = store.pop(old)
        for store in (self.freezes, self.freezes_below):
            if old in store:
                store[new] = store.pop(old)
            for mod, target in list(store.items()):
                if target == old:
                    store[mod] = new
        if old in self.freeze_notes:
            self.freeze_notes[new] = self.freeze_notes.pop(old)
        if old in self.ids:
            self.ids[new] = self.ids.pop(old)

    def reconcile(self, nodes) -> list:
        """Carry rules across mods that have been renamed. Returns the moves.

        Called with the mods actually installed. Every rule-mentioned mod
        that is present has its Nexus id remembered; every rule-mentioned
        mod that is *absent* is looked for by the id remembered last time.

        That is what makes a freeze survive a reinstall. MO2 names a mod
        after the archive it came from, so upgrading can rename it, and a
        rule keyed on the old name then matches nothing and is dropped on
        the floor without a word.

        A Nexus id is NOT one MO2 mod. One mod page routinely ships as
        several installed mods - four "At Your Own Pace" modules, four
        "Visions NPCs Recasted" packs - and on a real list that is about a
        third of everything. So the id only narrows the field; the name
        then has to pick out of it, and anything less than an obvious
        winner is left alone.

        Moving a rule onto the wrong mod would silently relocate a
        placement the user made by hand, which is worse than losing it -
        losing it they can see. Every doubtful case is skipped.
        """
        import difflib

        live = {node.name for node in nodes}
        by_id: dict = {}
        for node in nodes:
            nexus = int(getattr(node, "nexus_id", 0) or 0)
            if nexus:
                by_id.setdefault(nexus, []).append(node.name)

        mentioned = self.names()
        # Learn first, so a mod here now can still be found after a later
        # rename.
        for node in nodes:
            nexus = int(getattr(node, "nexus_id", 0) or 0)
            if nexus and node.name in mentioned:
                self.ids[node.name] = nexus

        def norm(name: str) -> str:
            keep = [c.lower() if (c.isalnum() or c == " ") else " "
                    for c in name]
            return " ".join("".join(keep).split())

        def score(a: str, b: str) -> float:
            return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()

        # Candidates are installed mods carrying no rules of their own: a
        # mod the user has already spoken about is not a mod that just
        # appeared under a new name.
        missing = sorted(n for n in mentioned - live if self.ids.get(n))
        taken: set = set()
        moves = []
        for name in missing:
            pool = [m for m in by_id.get(self.ids[name], ())
                    if m not in mentioned and m not in taken]
            if not pool:
                continue
            if len(pool) == 1:
                best, margin = pool[0], 1.0
            else:
                ranked = sorted(((score(name, m), m) for m in pool),
                                reverse=True)
                if ranked[0][0] < NAME_MATCH:
                    continue
                margin = ranked[0][0] - ranked[1][0]
                if margin < NAME_MARGIN:
                    continue          # two plausible mods, so pick neither
                best = ranked[0][1]
            self.rename(name, best)
            taken.add(best)
            mentioned = self.names()
            moves.append((name, best))

        # Ids exist to serve the rules, so forget the rest.
        for name in set(self.ids) - self.names():
            self.ids.pop(name, None)
        return moves

    def set_category(self, mod: str, category: str) -> None:
        """Record what a mod is, for one the metadata could not say."""
        category = (category or "").strip()
        if category:
            self.categories[mod] = category
        else:
            self.categories.pop(mod, None)

    def category_for(self, mod: str) -> str:
        return self.categories.get(mod, "")

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        pairs = []
        for (a, b), first in sorted(self.first.items()):
            second = b if first == a else a
            entry = {"first": first, "second": second}
            note = self.notes.get((a, b))
            if note:
                entry["note"] = note
            pairs.append(entry)
        pins = []
        for mod, where in sorted(self.pins.items()):
            entry = {"mod": mod, "at": where}
            if self.pin_notes.get(mod):
                entry["note"] = self.pin_notes[mod]
            pins.append(entry)
        freezes = []
        for side, held in (("above", self.freezes),
                           ("below", self.freezes_below)):
            for mod, target in sorted(held.items()):
                entry = {"mod": mod, side: target}
                if self.freeze_notes.get(mod):
                    entry["note"] = self.freeze_notes[mod]
                freezes.append(entry)
        freezes.sort(key=lambda e: e["mod"])
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ids": {m: i for m, i in sorted(self.ids.items())},
                       "pairs": pairs, "pins": pins, "freezes": freezes,
                       "categories": dict(sorted(self.categories.items()))},
                      fh, indent=1)
        os.replace(tmp, self.path)

    # -- use ------------------------------------------------------------
    def known(self, a: str, b: str) -> bool:
        return key(a, b) in self.first

    def set_winner(self, winner: str, loser: str, note: str = "") -> None:
        """Record which of a pair takes priority.

        The same fact as `set`, stated the way a user thinks about it.
        MO2's left pane loads top to bottom and later wins, so the winner
        is the one that loads *last* - and every time that has to be
        re-derived at a call site it is a chance to get it backwards.
        The dialog got it backwards exactly once, which is why this
        exists.
        """
        self.set(loser, winner, note)

    def set(self, first: str, second: str, note: str = "") -> None:
        pair = key(first, second)
        self.first[pair] = first
        if note:
            self.notes[pair] = note
        else:
            self.notes.pop(pair, None)

    def forget(self, a: str, b: str) -> None:
        pair = key(a, b)
        self.first.pop(pair, None)
        self.notes.pop(pair, None)

    def pin(self, mod: str, where: str = "top", note: str = "") -> None:
        self.pins[mod] = where
        if note:
            self.pin_notes[mod] = note

    def unpin(self, mod: str) -> None:
        self.pins.pop(mod, None)
        self.pin_notes.pop(mod, None)

    def freeze(self, mod: str, target: str, note: str = "",
               side: str = "above") -> None:
        """Keep ``mod`` directly above (or below) ``target``."""
        if mod == target:
            return
        self.unfreeze(mod)             # a mod sits on one side, not both
        held = self.freezes if side == "above" else self.freezes_below
        held[mod] = target
        if note:
            self.freeze_notes[mod] = note

    def freeze_chain(self, mod: str) -> list[str]:
        """The mods ``mod`` is held against, then what those are held against.

        Used to spot a freeze that would close a loop: if the target is
        already somewhere on this chain, the two rules cannot both hold.
        """
        out: list[str] = []
        at = mod
        while True:
            held = self.frozen_to(at)
            if held is None or held[0] in out:
                return out
            out.append(held[0])
            at = held[0]

    def frozen_to(self, mod: str):
        """(target, "above"/"below") for a frozen mod, else None."""
        if mod in self.freezes:
            return self.freezes[mod], "above"
        if mod in self.freezes_below:
            return self.freezes_below[mod], "below"
        return None

    def unfreeze(self, mod: str) -> None:
        self.freezes.pop(mod, None)
        self.freezes_below.pop(mod, None)
        self.freeze_notes.pop(mod, None)

    def edges(self, edge_type) -> list:
        """The stored answers as edges, ready to go in ahead of everything."""
        out = []
        for (a, b), first in sorted(self.first.items()):
            second = b if first == a else a
            out.append(edge_type(first, second, "user_rule",
                                 self.notes.get((a, b), "your choice")))
        return out
