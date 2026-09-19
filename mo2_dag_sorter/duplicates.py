"""Mods that ship exactly the same files as each other.

Different from :mod:`shadow`, which asks whether everything a mod ships
loses to the mods below it - that can be true because five other mods each
took a share, and it says the mod contributes nothing *here*, in this
order.  This asks a narrower and sharper question: do two mods carry an
identical set of files?  If they do, neither is really overriding the
other; they are the same content installed twice, and which one wins is
decided by whichever happens to sit lower.

It is nearly always worth a look.  Sometimes it is deliberate - a repack,
or a variant meant to replace its base wholesale - but far more often it is
an addon packaged with the base mod's files by mistake, or the same mod
installed twice under two names, and the user is carrying a duplicate they
did not mean to have.

Order does not come into it, so nothing here depends on the sort.  Output
and tools are left out: generated output is a copy of its sources by
definition, and a tool ships nothing the game reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Duplicate:
    names: list[str]              # every mod sharing this exact file set
    files: int
    sample: list[str] = field(default_factory=list)

    @property
    def headline(self) -> str:
        return "{} - {} identical file{}".format(
            " and ".join(self.names), self.files,
            "" if self.files == 1 else "s")


def find(nodes, output_tier: int = 6) -> list[Duplicate]:
    """Every group of enabled mods carrying an identical manifest."""
    groups: dict[frozenset, list[str]] = {}
    for node in nodes:
        if (node.is_separator or node.is_unmanaged or not node.enabled
                or node.is_tool or node.tier >= output_tier):
            continue
        manifest = frozenset(node.file_manifest)
        if not manifest:
            continue
        groups.setdefault(manifest, []).append(node.name)

    out = [Duplicate(names, len(manifest),
                     sorted(manifest)[:8])
           for manifest, names in groups.items() if len(names) > 1]
    out.sort(key=lambda d: (-d.files, d.names[0].lower()))
    return out
