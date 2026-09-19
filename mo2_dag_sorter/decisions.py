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
            json.dump({"pairs": pairs, "pins": pins, "freezes": freezes,
                       "categories": dict(sorted(self.categories.items()))},
                      fh, indent=1)
        os.replace(tmp, self.path)

    # -- use ------------------------------------------------------------
    def known(self, a: str, b: str) -> bool:
        return key(a, b) in self.first

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
