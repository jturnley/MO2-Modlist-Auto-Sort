"""Turning a left-pane selection into a set of freeze rules.

A freeze holds one mod directly beside one other mod.  Selecting several and
asking for them to sit below something is a statement about the group, so it
has to be taken apart into per-mod rules before anything else can use it:
the member on the target's side is held against the target, and the rest are
held against their neighbour.  The result is a chain, which is what makes the
selection travel as one block and keep its own internal order.

None of this touches MO2 or Qt - it is names and order, so it can be checked
without launching anything.
"""

from __future__ import annotations


def in_pane_order(nodes, names) -> list[str]:
    """The selection, in the order the pane shows it, sortable ones only.

    Click order is whatever order the user happened to ctrl-click in, and it
    is not what they mean by "keep these together".  Separators and unmanaged
    entries cannot be moved, so a rule about one could never be honoured.
    """
    chosen = set(names)
    return [n.name for n in nodes
            if n.name in chosen and not (n.is_separator or n.is_unmanaged)]


def stack_target(nodes, picked, side: str) -> str | None:
    """The mod the whole selection should sit beside.

    Read from the edge of the selection rather than from any one member, and
    anything else selected is stepped over: a gap inside the selection is not
    a reason to freeze the stack to one of its own members.
    """
    chosen = set(picked)
    order = [n.name for n in nodes]
    at = [i for i, name in enumerate(order) if name in chosen]
    if not at:
        return None
    by_name = {n.name: n for n in nodes}
    step = 1 if side == "above" else -1
    i = (max(at) if side == "above" else min(at)) + step
    while 0 <= i < len(order):
        node = by_name[order[i]]
        if order[i] not in chosen and not (node.is_separator
                                           or node.is_unmanaged):
            return node.name
        i += step
    return None


def stack_pairs(picked, target: str, side: str) -> list[tuple[str, str, str]]:
    """(mod, target, side) for every member, chaining the stack together."""
    if side == "above":
        # Last in pane order is the one nearest the target below it.
        return [(mod, picked[i + 1] if i + 1 < len(picked) else target,
                 "above") for i, mod in enumerate(picked)]
    return [(mod, picked[i - 1] if i else target, "below")
            for i, mod in enumerate(picked)]


def already_set(rules, picked, target: str, side: str) -> bool:
    """True when this exact stack is already frozen where it is asked to be."""
    want = stack_pairs(picked, target, side)
    return bool(want) and all(rules.frozen_to(mod) == (to, how)
                              for mod, to, how in want)


def loops(rules, pairs):
    """The first (mod, target) in this batch that would close a loop, or None.

    A freeze is a chain of "sits next to", and a chain that comes back to
    where it started cannot be honoured by any arrangement.  Easy to make by
    accident across two sittings: freeze A next to B today, freeze B next to
    A next month.  The batch is checked against the rules already on disk as
    well as against itself, and before anything is written - a half-applied
    stack leaves the list held in an arrangement nobody asked for.
    """
    held: dict[str, str] = {}
    for mod in list(getattr(rules, "freezes", {}) or {}) + list(
            getattr(rules, "freezes_below", {}) or {}):
        at = rules.frozen_to(mod)
        if at:
            held[mod] = at[0]
    held.update({mod: to for mod, to, _how in pairs})
    for mod, to, _how in pairs:
        if to == mod:
            return mod, to
        seen, at = {mod}, to
        while at is not None:
            if at in seen:
                return mod, to
            seen.add(at)
            at = held.get(at)
    return None
