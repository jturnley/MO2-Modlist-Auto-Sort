"""Which copy of a plugin wins, and what that costs in full plugin slots.

The right pane decides which plugins are active. The left pane decides which
*copy* of each one the game reads, and that copy decides whether it takes
one of the 254 full slots or none: an ESL-flagged plugin is light. Two mods
shipping the same ``Foo.esp`` - the original, and the copy the user flagged
ESL in xEdit and saved into a mod of its own - differ by nothing but that
flag.

So reordering mods can change the full-slot count without touching a
single plugin. A sort that moved a user's ESL-flagged copies above the
originals took them from 250 full plugins to 263, past the limit, with the
active count unchanged at 1755 and the right pane identical. Restoring the
right pane did nothing, because the right pane was never what changed.

Two answers live here. ``populate`` records which plugins each mod ships
light, so ``dag.light_edges`` can keep a light copy on top of a full one.
``compare`` counts the full slots before and after a sort, so the user sees
the number - and which plugins changed - before anything is written.
"""

from __future__ import annotations

import io
import os
import struct
from dataclasses import dataclass, field

PLUGIN_EXTS = (".esp", ".esm", ".esl")
LIGHT_FLAG = 0x200
# 0x00-0xFD for full plugins; 0xFE holds every light one, 0xFF is the
# game's own. Base masters and Creation Club content count.
FULL_LIMIT = 254


def is_light(path: str) -> bool | None:
    """True if the plugin takes no full slot, None if it cannot be read.

    An .esl is light whatever its header says. Anything else is light when
    its TES4 header carries the ESL flag.
    """
    if path.lower().endswith(".esl"):
        return True
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return None
    if len(head) < 12 or head[:4] != b"TES4":
        return None
    return bool(struct.unpack_from("<I", head, 8)[0] & LIGHT_FLAG)


def populate(nodes, mods_dir: str) -> None:
    """``light_plugins``: the root plugins each enabled mod ships light."""
    for node in nodes:
        node.light_plugins = set()
        if node.is_separator or node.is_unmanaged or not node.enabled:
            continue
        for plugin in node.plugins:
            if is_light(os.path.join(mods_dir, node.name, plugin)):
                node.light_plugins.add(plugin)


# -- counting -----------------------------------------------------------------

def plugin_lists(profile_dir: str):
    """(active, listed) from plugins.txt, or (None, None) if unreadable.

    ``listed`` is every name on the list, ticked or not. MO2 leaves the
    plugins the game loads regardless - base masters, Creation Club - off
    it entirely, which is how they are told apart below.
    """
    path = os.path.join(profile_dir, "plugins.txt")
    try:
        with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
            lines = [line.strip() for line in fh]
    except OSError:
        return None, None
    active, listed = set(), set()
    for line in lines:
        if not line or line.startswith("#"):
            continue
        name = line.lstrip("*").lower()
        listed.add(name)
        if line.startswith("*"):
            active.add(name)
    return active, listed


def _plugins_in(folder):
    if not folder or not os.path.isdir(folder):
        return []
    try:
        return [f for f in os.listdir(folder)
                if f.lower().endswith(PLUGIN_EXTS)]
    except OSError:
        return []


@dataclass
class Slots:
    full: int
    winners: dict          # plugin -> (where the winning copy is, light?)


def count(order, mods_dir, active, listed, data_dir, overwrite_dir) -> Slots:
    """Full slots taken in this left-pane order.

    Winners are worked out the way MO2 builds the game's Data folder: the
    game folder at the bottom, then each enabled mod in priority order,
    then overwrite on top. A plugin counts if it is ticked in the right
    pane, or if the game loads it without being asked - in the game folder
    and absent from plugins.txt altogether.
    """
    winners: dict = {}
    for name in _plugins_in(data_dir):
        low = name.lower()
        winners[low] = ("the game folder",
                        is_light(os.path.join(data_dir, name)))
    implicit = set(winners) - set(listed)
    for node in order:
        if node.is_separator or node.is_unmanaged or not node.enabled:
            continue
        lights = getattr(node, "light_plugins", None) or set()
        for plugin in node.plugins:
            winners[plugin] = (node.name, plugin in lights)
    for name in _plugins_in(overwrite_dir):
        winners[name.lower()] = ("overwrite",
                                 is_light(os.path.join(overwrite_dir, name)))
    loaded = (set(active) | implicit) & set(winners)
    full = sum(1 for p in loaded if winners[p][1] is False)
    return Slots(full, {p: winners[p] for p in loaded})


@dataclass
class SlotChange:
    before: int
    after: int
    became_full: list = field(default_factory=list)   # (plugin, now, was)
    became_light: list = field(default_factory=list)  # (plugin, now, was)

    @property
    def changed(self) -> bool:
        return self.before != self.after or bool(self.became_full
                                                 or self.became_light)

    @property
    def over(self) -> bool:
        return self.after > FULL_LIMIT

    @property
    def headline(self) -> str:
        return "full plugin slots {} -> {} (limit {})".format(
            self.before, self.after, FULL_LIMIT)


def compare(before_order, after_order, mods_dir, profile_dir, data_dir,
            overwrite_dir) -> SlotChange | None:
    """What the new order does to the full-slot count, or None if the
    plugin list cannot be read."""
    active, listed = plugin_lists(profile_dir)
    if active is None:
        return None
    was = count(before_order, mods_dir, active, listed, data_dir,
                overwrite_dir)
    now = count(after_order, mods_dir, active, listed, data_dir,
                overwrite_dir)
    change = SlotChange(was.full, now.full)
    for plugin in sorted(now.winners):
        old_where, old_light = was.winners.get(plugin, (None, None))
        new_where, new_light = now.winners[plugin]
        if old_light is True and new_light is False:
            change.became_full.append((plugin, new_where, old_where))
        elif old_light is False and new_light is True:
            change.became_light.append((plugin, new_where, old_where))
    return change
