"""Component D: build the graph, then flatten it with Kahn's algorithm.

An edge is ``parent -> child``: the parent loads earlier and the child, being
later, overrides it.  Three kinds are built.

**tier_constraint** is the backbone, and it is *not* an edge.  Emitting one
per ordered pair would cost hundreds of thousands of edges and say nothing the
tie-breaker cannot say for free, so tier is the first sort key instead and the
graph carries only the constraints that cut across it.

**file_conflict** needs a direction, and two mods writing the same file do not
supply one - that is the judgement call at the centre of this component.  The
rule: where the tiers differ, the higher tier wins, because that is what the
tier matrix means.  Where they match, the list's existing order is kept, on
the grounds that the user put it that way on purpose and nothing here knows
better.  So a conflict edge never invents an opinion; it either restates the
tier matrix or preserves what was already there.

**master_requirement** is the one hard fact available: a plugin cannot load
above a plugin it is built on.  These are the only edges allowed to contradict
the tier matrix, and when they do, the cycle detector is what catches it.
"""

from __future__ import annotations

import heapq
import re
from collections import defaultdict
from dataclasses import dataclass

from . import tiers


@dataclass(frozen=True)
class DependencyEdge:
    parent: str        # loads earlier / lower priority
    child: str         # loads later / higher priority, so it wins
    reason: str        # "file_conflict" | "master_requirement" | "tier_constraint"
    detail: str = ""


class CycleError(Exception):
    """Raised with the mods left unplaced when the graph is not acyclic."""

    def __init__(self, nodes: list[str], edges: list[DependencyEdge]) -> None:
        super().__init__("circular dependency among {} mods".format(len(nodes)))
        self.nodes = nodes
        self.edges = edges


def sortable(nodes) -> list:
    """The nodes this engine is allowed to move.

    Separators are the user's own headings and unmanaged entries are pinned by
    MO2 above everything else; both are put back exactly where they were.
    """
    return [n for n in nodes if not n.is_separator and not n.is_unmanaged]


# What makes an overlap read as "this mod exists to override that one":
# the mod is a handful of files, a good fraction of them collide, and they
# are a rounding error of the other mod.
OVERLAY_FILES = 12
OVERLAY_SHARE = 0.25
OVERLAY_RATIO = 8


def _overlay(a, b, shared: int):
    """(the mod that loads first, the one that loads after), or None.

    A file conflict says two mods write the same path; it does not say which
    way round they belong.  The shape of the overlap sometimes does.  "High
    Poly Head UV Stretch Fix" ships two meshes, one of which High Poly Head
    also ships among its 926, and a fix that replaces one file of a mod
    loads after it.

    The size cap is what keeps this honest, and it was put there after the
    rule without it inverted a PBR stack.  "RUSTIC ANIMATED POTIONS" shares
    77% of its files with an 8000-file PBR pack, which is the same shape,
    but it is a 148-file texture mod in its own right and the PBR pack is
    meant to override it.  A mod of a dozen files that lands inside a big
    one is a patch; a mod of a hundred is content, and the question of which
    wins is not answered by counting files.
    """
    if not a.file_manifest or not b.file_manifest:
        return None
    share_a = shared / len(a.file_manifest)
    share_b = shared / len(b.file_manifest)
    small, big, small_share, big_share = (
        (a, b, share_a, share_b) if share_a > share_b
        else (b, a, share_b, share_a))
    if len(small.file_manifest) > OVERLAY_FILES:
        return None
    if small_share < OVERLAY_SHARE:
        return None
    if small_share < OVERLAY_RATIO * big_share:
        return None
    return big, small


def conflict_edges(nodes) -> list[DependencyEdge]:
    """One edge per conflicting pair, by tier, surface rank, shape, order."""
    by_file: dict[str, list] = defaultdict(list)
    for node in nodes:
        if node.is_tool:          # nothing it ships is loaded by the game
            continue
        for path in node.file_manifest:
            by_file[path].append(node)

    shared: dict[tuple[str, str], int] = defaultdict(int)
    seen: dict[tuple[str, str], tuple] = {}
    for path, owners in by_file.items():
        if len(owners) < 2:
            continue
        # A file every mod in a large list touches says nothing useful and
        # would emit O(n^2) edges on its own.
        if len(owners) > 40:
            continue
        for i, a in enumerate(owners):
            for b in owners[i + 1:]:
                pair = tuple(sorted((a.name, b.name)))
                shared[pair] += 1
                seen[pair] = (a, b)

    out = []
    for pair, count in shared.items():
        a, b = seen[pair]
        if a.tier != b.tier:
            lo, hi = (a, b) if a.tier < b.tier else (b, a)
        elif a.subtier != b.subtier:
            # Two texture mods fighting over the same surfaces: what they
            # are decides, not which was installed first.  See
            # tiers.surface_rank.
            lo, hi = (a, b) if a.subtier < b.subtier else (b, a)
        else:
            shape = _overlay(a, b, count)
            if shape is not None:
                lo, hi = shape
            else:
                lo, hi = ((a, b) if a.original_index < b.original_index
                          else (b, a))
        out.append(DependencyEdge(lo.name, hi.name, "file_conflict",
                                  "{} shared file(s)".format(count)))
    return out


def master_edges(nodes) -> list[DependencyEdge]:
    """A mod loads below every installed mod whose plugin it is built on."""
    owner_of: dict[str, str] = {}
    for node in nodes:
        for name in node.plugins:
            owner_of[name] = node.name

    out: list[DependencyEdge] = []
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        for master in node.masters:
            parent = owner_of.get(master)
            if not parent or parent == node.name:
                continue
            key = (parent, node.name)
            if key in seen:
                continue
            seen.add(key)
            out.append(DependencyEdge(parent, node.name, "master_requirement",
                                      "declares {} as a master".format(master)))
    return out


# Asset paths that carry a plugin filename as a directory component. The
# game resolves these by name, so a mod dropping files under another mod's
# plugin folder is addressing that mod specifically - it is the loose-file
# equivalent of declaring a master.
PLUGIN_IN_PATH = re.compile(
    r"(?:^|/)(?:sound/voice|facegeom|facetint|grass|dialogueviews)"
    r"/([^/]+\.es[pml])/", re.IGNORECASE)


def asset_path_edges(nodes) -> list[DependencyEdge]:
    """A mod loads below whatever owns the plugin its asset paths name.

    This is the only evidence available for an add-on that ships no plugin at
    all.  A voice pack for a quest mod is filed on Nexus under Audio, which
    makes it look like ambient sound and sorts it above the quest it is
    replacing lines in - it has no name saying "patch" and no master to
    follow.  Its file paths say it outright:

        sound/voice/houseofhorrorsquestexpansion.esp/maleuniquemolagbal/...
    """
    owner_of: dict[str, str] = {}
    for node in nodes:
        for name in node.plugins:
            owner_of[name] = node.name

    out: list[DependencyEdge] = []
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        for path in node.file_manifest:
            found = PLUGIN_IN_PATH.search(path)
            if not found:
                continue
            plugin = found.group(1).lower()
            parent = owner_of.get(plugin)
            if not parent or parent == node.name:
                continue
            key = (parent, node.name)
            if key in seen:
                continue
            seen.add(key)
            out.append(DependencyEdge(
                parent, node.name, "asset_path",
                "ships assets under {}/".format(plugin)))
    return out


def requirement_edges(nodes, needs) -> list[DependencyEdge]:
    """A mod's Nexus page lists another installed mod as a requirement.

    Read as "the requirement loads first": it has to exist for the requiring
    mod to work, and the requiring mod is normally built on top of it.  Not
    a law - a mod can require another in order to replace part of it - so
    this ranks below anything the user has decided.
    """
    by_id: dict[int, str] = {}
    for node in nodes:
        if node.nexus_id and node.nexus_id not in by_id and not node.is_tool:
            by_id[node.nexus_id] = node.name
    out: list[DependencyEdge] = []
    for node in nodes:
        # A tool is run, not loaded. "PGPatcher is required" is a statement
        # about how the load order was built, not about what has to load
        # before what, and following it holds mods apart for no reason.
        if not node.nexus_id or node.is_tool:
            continue
        for needed, note in needs.get(node.nexus_id, ()):
            base = by_id.get(needed)
            if not base or base == node.name:
                continue
            out.append(DependencyEdge(
                base, node.name, "nexus_requirement",
                note.strip() or "listed as a requirement on its Nexus page"))
    return out


def shader_edges(nodes) -> list[DependencyEdge]:
    """The shader framework loads before everything it draws.

    PBR and parallax are not file formats, they are shaders: the meshes and
    textures are data for code that Community Shaders installs.  The
    framework has to be in place before anything that relies on it, and
    that holds for every mod using those shaders, whatever category it
    happens to be filed under.

    Nexus requirement lists say this one pair at a time and only where an
    author remembered to fill them in, which leaves the rest to be ordered
    by file overlap - and a PBR set that shares no file with the framework
    is then free to sort above it.
    """
    frameworks = [n.name for n in nodes if tiers.is_shader_framework(n.name)]
    if not frameworks:
        return []
    out: list[DependencyEdge] = []
    for node in nodes:
        if node.is_tool or node.name in frameworks:
            continue
        if not tiers.uses_shaders(node.name):
            continue
        for framework in frameworks:
            out.append(DependencyEdge(
                framework, node.name, "shader_framework",
                "PBR and parallax surfaces are drawn by {}, so it has to "
                "load first".format(framework)))
    return out


def build_edges(nodes, needs=None) -> list[DependencyEdge]:
    return (conflict_edges(nodes) + master_edges(nodes)
            + asset_path_edges(nodes) + name_extension_edges(nodes)
            + requirement_edges(nodes, needs or {})
            + shader_edges(nodes))


def apply_freezes(order, above, below=None, edges=()) -> list:
    """Move each frozen mod so it sits directly beside its target.

    Run after the sort rather than as an edge, because a freeze is not a
    claim about overriding - it is the user saying "these two belong
    together, keep them that way".  An edge would only say the mod loads
    somewhere before its target, which is what the sorter was already free
    to do and not what was asked for.

    ``above`` holds {mod: the target it sits directly above} and ``below``
    the mirror.  Several mods frozen to the same side of the same target
    become one stack, in the order the sort gave them, sitting immediately
    on that side of it.  A target that is itself frozen carries its whole
    stack along, so a chain stays intact.

    A freeze naming a mod that is not here, or one that loops back on
    itself, is ignored rather than obeyed halfway.  So is one that would
    break a real dependency: moving a mod to sit beside its target can
    leave something that has to load after it stranded above it, and a
    silently broken master is worse than an unhonoured preference.  The
    caller compares the result against what was asked and reports the
    difference.
    """
    above = dict(above or {})
    below = dict(below or {})
    if not above and not below:
        return list(order)

    present = {n.name for n in order}
    def usable(pairs):
        return {m: t for m, t in pairs.items()
                if m in present and t in present and m != t}
    above, below = usable(above), usable(below)
    # A mod can only be in one place, so a mod frozen on both sides keeps
    # the "above" rule; the menu does not offer both, but a hand-edited
    # rules file can.
    for mod in list(below):
        if mod in above:
            below.pop(mod)

    attach = dict(above)
    attach.update(below)

    # A loop ("A above B, B above A") has no arrangement that satisfies it,
    # and honouring half of one would be worse than honouring none: the
    # whole cycle is dropped, so the mods sort normally.
    for start in list(attach):
        path, at = [], start
        while at is not None and at not in path:
            path.append(at)
            at = attach.get(at)
        if at is not None:                       # walked into a cycle
            for name in path[path.index(at):]:
                attach.pop(name, None)
                above.pop(name, None)
                below.pop(name, None)

    rank = {n.name: i for i, n in enumerate(order)}
    by_name = {n.name: n for n in order}
    over: dict[str, list[str]] = defaultdict(list)
    under: dict[str, list[str]] = defaultdict(list)

    def block(name: str) -> list:
        """The mod, with its two stacks wrapped around it."""
        out: list = []
        for member in over.get(name, ()):
            out.extend(block(member))
        out.append(by_name[name])
        for member in under.get(name, ()):
            out.extend(block(member))
        return out

    def arrange() -> list:
        over.clear()
        under.clear()
        for mod, target in above.items():
            over[target].append(mod)
        for mod, target in below.items():
            under[target].append(mod)
        for group in (over, under):
            for target in group:
                group[target].sort(key=lambda m: rank[m])
        held = set(above) | set(below)
        out: list = []
        for node in order:
            if node.name in held:
                continue               # emitted with the mod it hangs off
            out.extend(block(node.name))
        return out

    # Honouring a freeze can strand a dependency: pulling a mod up to sit
    # above its target leaves anything that must load after it above it
    # instead. Where that dependency is one of the hard ones the freeze is
    # dropped, because a plugin cannot load without what it was built on.
    # Everything else yields to the freeze instead - the user placing a mod
    # by hand is a better answer than the direction the sorter guessed.
    wanted = [(e.parent, e.child) for e in edges
              if e.reason in HARD_REASONS]
    for _attempt in range(len(above) + len(below) + 1):
        out = arrange()
        where = {n.name: i for i, n in enumerate(out)}
        broken = {name for parent, child in wanted
                  if parent in where and child in where
                  and where[parent] > where[child]
                  for name in (parent, child)}
        guilty = {m for group in (above, below) for m, t in group.items()
                  if m in broken or t in broken}
        if not guilty:
            return out
        for name in guilty:
            above.pop(name, None)
            below.pop(name, None)
    return arrange()


def topological_sort(nodes, edges, pins=None, freezes=None,
                     freezes_below=None) -> list:
    """Kahn's algorithm, with (tier, surface rank, original index, name).

    A pin overrides the tier in that key and nothing else: a pinned mod goes
    as early or as late as the constraints allow, but it cannot jump a
    genuine dependency.  If something really must load above a top-pinned
    mod, the pin loses and the caller can say so - silently producing an
    order that ignores a master would be worse than not honouring the pin.

    The tie-breaker is what makes the result deterministic and what makes it
    *stable*: with no constraint to satisfy, the ready mod with the lowest
    tier wins, and within a tier the one already earliest in the list. A run
    over an already-sorted list therefore changes nothing.
    """
    by_name = {n.name: n for n in nodes}
    children: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {n.name: 0 for n in nodes}

    for edge in edges:
        if edge.parent not in by_name or edge.child not in by_name:
            continue
        children[edge.parent].append(edge.child)
        in_degree[edge.child] += 1

    ranks = {"top": -1, "bottom": 99}

    def key(name: str):
        node = by_name[name]
        tier = ranks.get((pins or {}).get(name), node.tier)
        return (tier, node.subtier, node.original_index, node.name.lower())

    ready = [key(n.name) + (n.name,) for n in nodes
             if in_degree[n.name] == 0]
    heapq.heapify(ready)

    order: list = []
    while ready:
        name = heapq.heappop(ready)[-1]
        order.append(by_name[name])
        for child in children[name]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                heapq.heappush(ready, key(child) + (child,))

    if len(order) != len(nodes):
        placed = {n.name for n in order}
        stuck = [n.name for n in nodes if n.name not in placed]
        blocking = [e for e in edges
                    if e.parent in set(stuck) and e.child in set(stuck)]
        raise CycleError(stuck, blocking)
    return apply_freezes(order, freezes, freezes_below, edges)


def acyclic_edges(nodes, edges, decisions=None) -> tuple[
        list[DependencyEdge], list[DependencyEdge]]:
    """(edges to sort with, edges dropped to make the graph acyclic).

    The two edge kinds can contradict each other, and on a real list they do:
    "patches sort last" says a patch loads below the mod it patches, while a
    plugin that names that patch as a master must load below *it*.  Both
    statements are true and they cannot both be honoured.

    A master is a fact read out of a file header; a tier is a classification
    someone assigned.  So the master edges go in first and keep their
    authority, and a conflict edge is admitted only if it does not close a
    loop against what is already there.  Nothing is silently lost - every
    dropped edge is returned, and the caller reports it.

    Conflict edges are considered in descending order of how many files they
    cover, so when one has to go it is the one governing the fewest assets.

    An edge repeating a pair already stated is not reported: a master record
    and an asset path naming the same two mods are two signals agreeing.
    """
    by_name = {n.name: n for n in nodes}
    order = {n.name: i for i, n in enumerate(nodes)}
    children: dict[str, set[str]] = defaultdict(set)

    # A pin is the user saying a mod belongs at one end of the list and
    # meaning it: the crash logger has to load first or it catches nothing.
    # An edge that would put something before a top-pinned mod contradicts
    # that outright, so it never gets considered - and it is not reported as
    # unsettled either, because the user already settled it.
    pins = dict(getattr(decisions, "pins", {}) or {})
    def contradicts_pin(edge) -> bool:
        return (pins.get(edge.child) == "top"
                or pins.get(edge.parent) == "bottom")
    edges = [e for e in edges if not contradicts_pin(e)]

    def reaches(start: str, goal: str) -> bool:
        """Is ``goal`` already downstream of ``start``?"""
        seen = {start}
        stack = [start]
        while stack:
            for nxt in children[stack.pop()]:
                if nxt == goal:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    def admit(edge: DependencyEdge) -> str:
        """"kept", "duplicate" (the same pair, said twice) or "cycle"."""
        if edge.parent not in by_name or edge.child not in by_name:
            return "duplicate"
        if edge.child in children[edge.parent]:
            # Both an ESP master and an asset path can name the same pair;
            # that is the two signals agreeing, not a conflict to report.
            return "duplicate"
        if reaches(edge.child, edge.parent):
            return "cycle"
        children[edge.parent].add(edge.child)
        return "kept"

    kept: list[DependencyEdge] = []
    dropped: list[DependencyEdge] = []

    # A standing answer from the user outranks everything else here. It is
    # the only edge kind that can push a master aside, because it is the only
    # one that knows something the files do not.
    if decisions is not None:
        for edge in decisions.edges(DependencyEdge):
            verdict = admit(edge)
            if verdict == "kept":
                kept.append(edge)
            elif verdict == "cycle":
                dropped.append(edge)

    # Both of these are read off disk rather than inferred, so they go in
    # first and a conflict edge yields to them.
    factual = {"master_requirement", "asset_path",
               "shader_framework"}
    masters_first = sorted(
        (e for e in edges if e.reason in factual),
        key=lambda e: (order.get(e.parent, 0), order.get(e.child, 0)))
    for edge in masters_first:
        verdict = admit(edge)
        if verdict == "kept":
            kept.append(edge)
        elif verdict == "cycle":
            dropped.append(edge)

    # The author's requirement list is a statement about their own mod, so
    # it outranks a guess made from a name or a file overlap - but it says
    # what must be installed, not what must be overridden, so it yields to
    # both the user and anything read out of a plugin header.
    for edge in sorted((e for e in edges if e.reason == "nexus_requirement"),
                       key=lambda e: (e.parent, e.child)):
        verdict = admit(edge)
        if verdict == "kept":
            kept.append(edge)
        elif verdict == "cycle":
            dropped.append(edge)

    # A name is the author's own statement, so it outranks a file overlap -
    # but it is still a string, so it yields to anything read off disk.
    for edge in sorted((e for e in edges if e.reason == "name_extension"),
                       key=lambda e: (e.parent, e.child)):
        verdict = admit(edge)
        if verdict == "kept":
            kept.append(edge)
        elif verdict == "cycle":
            dropped.append(edge)

    def weight(edge: DependencyEdge) -> int:
        head = edge.detail.split(" ", 1)[0]
        return int(head) if head.isdigit() else 0

    ranked = factual | {"name_extension", "nexus_requirement"}
    conflicts = sorted((e for e in edges if e.reason not in ranked),
                       key=lambda e: (-weight(e), e.parent, e.child))
    for edge in conflicts:
        verdict = admit(edge)
        if verdict == "kept":
            kept.append(edge)
        elif verdict == "cycle":
            dropped.append(edge)
    return kept, dropped


# Words that mean a mod is asserting what it is rather than what it extends.
# "Cloaks of Skyrim SSE PBR" begins with another mod's whole name but belongs
# with the PBR sets, and "AI Overhaul - Patch Hub" patches AI Overhaul
# against third parties rather than adding to it.
SELF_ASSERTING = ("patch", "hub", "pbr", "parallax", "complex material",
                  "enb", "reshade", "compatibility", "hotfix", "fix",
                  "replacer", "unofficial")


def name_extension_edges(nodes) -> list[DependencyEdge]:
    """A mod whose name begins with another installed mod's name extends it.

    Weaker evidence than a master record - it is a string, and a string can
    be a coincidence - but it is the author saying outright what their mod is
    for, and it settles cases nothing on disk can reach: "Heavy Armory -
    Glaive Addons" is an addon to "Heavy Armory - New Weapons" and says so,
    even where the two plugins master each other in a loop.

    Guarded hard, because a loose match here would reorder half a list: the
    base name must be at least two words and eight characters, and must end
    on a word boundary in the extension's name.
    """
    live = [n for n in nodes if n.enabled and not n.is_separator
            and not n.is_unmanaged]
    out: list[DependencyEdge] = []
    for node in live:
        low = node.name.lower()
        if any(word in low for word in SELF_ASSERTING):
            continue
        best = None
        for other in live:
            if other is node:
                continue
            base = other.name.lower()
            if len(base) < 8 or len(base.split()) < 2:
                continue
            if not low.startswith(base) or len(low) == len(base):
                continue
            if low[len(base)].isalnum():
                continue
            if best is None or len(base) > len(best.name):
                best = other
        if best is not None:
            out.append(DependencyEdge(
                best.name, node.name, "name_extension",
                "its name says it extends {}".format(best.name)))
    return out


def unhonoured_freezes(order, freezes, below=None) -> list[tuple[str, str]]:
    """The freezes the final order does not satisfy, as (mod, target).

    A freeze can lose to a real dependency, and the user has to be told: a
    rule they set from the left pane quietly doing nothing is the worst of
    the available outcomes.

    "Directly above" means directly above the target *and its stack*, not
    literally the next line: two mods frozen to the same side of one target
    are both honoured even though only one of them touches it.  So the test
    is that nothing sits between the mod and its target except other mods
    hanging off that same target.
    """
    above = dict(freezes or {})
    below = dict(below or {})
    where = {n.name: i for i, n in enumerate(order)}

    held: dict[str, list[str]] = defaultdict(list)
    for mod, target in list(above.items()) + list(below.items()):
        held[target].append(mod)

    def stack(target: str) -> set[str]:
        """Everything hanging off ``target``, however deeply."""
        out: set[str] = set()
        stack_ = list(held.get(target, ()))
        while stack_:
            name = stack_.pop()
            if name in out:
                continue
            out.add(name)
            stack_.extend(held.get(name, ()))
        return out

    out = []
    for mod, target in sorted(above.items()) + sorted(below.items()):
        if mod not in where or target not in where:
            continue
        lo, hi = sorted((where[mod], where[target]))
        between = {n.name for n in order[lo + 1:hi]}
        if between - stack(target):
            out.append((mod, target))
    return out


# What each kind of edge means, in words a message box can show. The mod
# named second is the one that has to load first.
EDGE_WORDS = {
    "master_requirement": "{child}'s plugin names a master that {parent} owns",
    "asset_path": "{child} ships files inside {parent}'s plugin folder",
    "nexus_requirement": "{child}'s Nexus page lists {parent} as a "
                         "requirement",
    "name_extension": "{child}'s name says it extends {parent}",
    "file_conflict": "{child} and {parent} share files, so one has to "
                     "override the other",
    "user_rule": "you decided this pair yourself",
}


# What a freeze cannot argue with.
#
# A freeze is the user stating where a mod goes, so it outranks anything
# the sorter worked out for itself - in particular a file_conflict, which
# is a fact that two mods share files plus a *guess* at the direction, made
# from tier, then shape, then install order. Being told "this one wins" is
# better evidence than that guess, not worse, so the guess yields. The same
# goes for a requirement list, which is the author saying what to install
# rather than what overrides what.
#
# These three are different. A master is read out of a plugin header and an
# asset path out of a folder name: both say which mod a file belongs to,
# and moving past one leaves a plugin without what it was built on. The
# shader framework has to be in place before the code that draws a surface
# runs at all. None of those is a preference to be overruled, so a freeze
# that would break one is refused and the user told why.
HARD_REASONS = frozenset(("master_requirement", "asset_path",
                          "shader_framework"))


def freeze_blockers(order, edges, mod: str, target: str,
                    side: str = "above") -> list:
    """The edges that stop ``mod`` sitting directly beside ``target``.

    Worked out against the order as it stands: the mod would land just
    before the target (or just after it), so anything that must load before
    the mod but sits beyond that point, or must load after it but sits
    before it, is in the way.  Returned so the caller can say which mod is
    the obstacle and why, rather than reporting a rule that quietly does
    nothing.
    """
    pos = {n.name: i for i, n in enumerate(order)}
    if mod not in pos or target not in pos:
        return []
    dest = pos[target] if side == "above" else pos[target] + 1
    out = []
    for edge in edges:
        if edge.reason not in HARD_REASONS:
            continue
        if edge.parent == mod or edge.child == mod:
            other = edge.child if edge.parent == mod else edge.parent
            at = pos.get(other)
            if at is None:
                continue
            if edge.child == mod and at >= dest:
                out.append(edge)       # must load before it, but sits after
            elif edge.parent == mod and at < dest:
                out.append(edge)       # must load after it, but sits before
    return out


def blocker_text(order, edge, mod: str) -> str:
    """One line naming the mod in the way and what puts it there."""
    pos = {n.name: i for i, n in enumerate(order)}
    other = edge.child if edge.parent == mod else edge.parent
    where = "before" if edge.child == mod else "after"
    reason = EDGE_WORDS.get(edge.reason, edge.reason).format(
        child=edge.child, parent=edge.parent)
    line = "{} has to load {} {}, because {}.".format(
        other, where, mod, reason)
    if edge.detail and edge.reason in ("nexus_requirement", "user_rule"):
        line += '  The note on it reads: "{}".'.format(edge.detail)
    return line + "  It is at position {} in the list.".format(
        pos.get(other, 0) + 1)
