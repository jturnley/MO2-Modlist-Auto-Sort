"""Mods that contribute nothing: every file they ship is overridden.

A mod whose every file loses to something below it is installed, enabled,
and invisible.  Sometimes that is the intent - a base body under a full
replacer - but far more often it means a mod landed too high, or a newer
version of the same thing is installed twice, and the user is looking at a
list entry that does nothing at all.

Generated output does not count as an overrider.  PGPatcher, DynDOLOD and
the rest read the whole list and write out a merged copy of what they find,
so of course they shadow their sources: that is what they are for, and a mod
shadowed only by output is working exactly as intended.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Shadowed:
    name: str
    priority: int
    files: int
    by: list[str]            # the mods doing the overriding, most files first

    @property
    def headline(self) -> str:
        return "{} ({} file{}) is fully overridden by {}".format(
            self.name, self.files, "" if self.files == 1 else "s",
            ", ".join(self.by[:3]))


def find(nodes, output_tier: int) -> list[Shadowed]:
    """Every enabled mod whose whole manifest loses, in priority order.

    ``nodes`` must already be in the order being judged - index 0 loads
    first, so a file is shadowed by any later node that also ships it.
    """
    live = [n for n in nodes
            if n.enabled and not n.is_separator and not n.is_unmanaged
            and n.file_manifest]

    # Walk from the bottom up, so "has anything below me claimed this file"
    # is a single set membership test rather than a search.
    claimed: dict[str, str] = {}
    out: list[Shadowed] = []
    for node in reversed(live):
        winners: dict[str, int] = {}
        for path in node.file_manifest:
            owner = claimed.get(path)
            if owner is not None:
                winners[owner] = winners.get(owner, 0) + 1
        if winners and len(
                {p for p in node.file_manifest if p in claimed}) == len(
                    set(node.file_manifest)):
            out.append(Shadowed(
                node.name, node.original_index, len(set(node.file_manifest)),
                [name for name, _ in sorted(winners.items(),
                                            key=lambda kv: -kv[1])]))
        # Output is not an overrider: it is a copy of everything above it.
        if node.tier < output_tier:
            for path in node.file_manifest:
                claimed.setdefault(path, node.name)
    out.reverse()
    return out
