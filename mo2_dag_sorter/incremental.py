"""Place one newly installed mod, and ask only about that mod.

A load order is not built in one sitting.  Mods arrive one at a time over
weeks, and the questions the sorter cannot answer arrive with them - so they
should be asked then, one or two at a time, while the user still has the mod
page open and remembers why they installed it.  Thirty-three questions in a
single dialog is a chore; thirty-three questions spread over eighty installs
is barely noticeable, and each answer is a better one for being asked in
context.

So this module does two things the full sort deliberately does not:

Ask narrowly.  Of everything the sorter could not settle, only the pairs
naming the new mod are the user's problem right now.  The rest are about
mods they installed months ago and are not thinking about.

Move narrowly.  A full sort renumbers most of the list, which is correct but
alarming to watch happen after installing one mod.  Here the existing order
is left exactly as it is and the newcomer is slotted into it, at the spot
the graph says it belongs.
"""

from __future__ import annotations


def questions_for(unresolved, mod_name: str) -> list:
    """The pairs the user cannot avoid deciding, because this mod is in them."""
    return [e for e in unresolved
            if mod_name in (e.parent, e.child)]


def place_one(current, ordered, mod_name: str, edges=()) -> list:
    """``current`` with ``mod_name`` moved to where it belongs in it.

    Everything else keeps its position.  Two things decide the slot.

    The edges touching the new mod are hard walls: it must sit below every
    mod that has to load before it and above every mod that has to load
    after it, measured in the list as it stands, not in some idealised one.

    Inside that window the sorted order chooses, by putting the newcomer
    among the mods it ranks alongside - which is what makes an armour mod
    land in the armour stretch of the list rather than merely somewhere
    legal.
    """
    names = [n.name for n in current]
    if mod_name not in names:
        return list(current)

    rest = [n for n in current if n.name != mod_name]
    node = next(n for n in current if n.name == mod_name)
    here = {n.name: i for i, n in enumerate(rest)}

    floor, ceiling = 0, len(rest)
    for edge in edges:
        if edge.child == mod_name and edge.parent in here:
            floor = max(floor, here[edge.parent] + 1)
        elif edge.parent == mod_name and edge.child in here:
            ceiling = min(ceiling, here[edge.child])
    if floor > ceiling:                # the graph contradicts the list
        floor = ceiling

    # Inside the legal window, pick the slot that leaves the list in the
    # best agreement with the sorted order: as many lower-ranked mods above
    # the newcomer as possible, and as many higher-ranked ones below it.
    # Taking the first legal slot instead would drop an armour mod into the
    # middle of the SKSE plugins purely because nothing forbade it.
    rank = {n.name: i for i, n in enumerate(ordered)}
    mine = rank.get(mod_name)
    if mine is None:
        return rest[:ceiling] + [node] + rest[ceiling:]

    above = [1 if rank.get(n.name, mine) < mine else 0 for n in rest]
    below = [1 if rank.get(n.name, mine) > mine else 0 for n in rest]
    score = sum(below[floor:ceiling])          # slot == floor
    best, at = score, floor
    for i in range(floor, ceiling):
        score += above[i] - below[i]
        if score > best:
            best, at = score, i + 1

    return rest[:at] + [node] + rest[at:]
