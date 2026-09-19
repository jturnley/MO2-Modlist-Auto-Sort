"""The end-to-end sequence, with no MO2 and no Qt in it.

    parse -> metadata -> nexus -> conflicts -> build -> sort -> export

Component E's rule about fixed entries lives here rather than in the writer:
separators and unmanaged entries keep their exact line positions, and the
sorted mods are poured into the slots that are left. That way a run never
disturbs the user's own headings and never produces a file MO2 will reject
because something was moved above the Creation Club block.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import (dag, decisions as decisions_mod, duplicates, masters,
               modlist, nexus, nexus_api,
               requirements, scan, shadow, tiers, uncategorised)


@dataclass
class SortResult:
    nodes: list            # the full list, in its new priority order
    header: list           # the comment lines that topped the original file
    moved: list            # (name, old index, new index) for everything that moved
    edges: list
    dropped: list          # edges that had to yield to break a cycle
    shadowed: list         # mods that contribute nothing in the new order
    duplicates: list       # groups of mods carrying identical files
    uncategorised: list    # mods placed by the file tree, with no category
    unresolved: list       # dropped edges with no standing answer from the user
    unfrozen: list         # (mod, target) freezes a real dependency overrode
    report: str

    @property
    def changed(self) -> bool:
        return bool(self.moved)


def _reassemble(original: list, ordered: list) -> list:
    """Put the sorted mods back into the non-fixed line positions."""
    out: list = []
    flow = iter(ordered)
    for node in original:
        out.append(node if (node.is_separator or node.is_unmanaged)
                   else next(flow))
    return out


def sort_profile(mo2_root: str, profile: str = "Default",
                 api_key: str | None = None,
                 domain: str = "skyrimspecialedition",
                 cache_dir: str | None = None,
                 progress=None, resolver=None,
                 decisions=None, client=None) -> SortResult:
    """Run the whole sequence over one profile.

    ``resolver`` is an optional object with ``.resolve(ids)`` - inside MO2
    that is a :class:`nexus_bridge.BridgeResolver`, which spends MO2's own
    Nexus key instead of asking for a second copy of it. Without one, the
    direct HTTP path runs, which needs ``api_key`` and is what the offline
    command-line runner uses.

    ``client`` is a Nexus client from the MO2 Nexus API Extender.  When one
    is given, every Nexus call goes through it: one stored key and one
    response cache shared with every other plugin, instead of a second copy
    of each kept here.  Without one, this plugin's own transport runs, which
    is what happened before the Extender existed.
    """
    mods_dir = os.path.join(mo2_root, "mods")
    list_path = os.path.join(mo2_root, "profiles", profile, "modlist.txt")
    # Where MO2 itself hands a plugin its data, so the command-line runner
    # and the plugin read and write the same cache and the same user rules.
    cache_dir = cache_dir or os.path.join(mo2_root, "plugins", "data")

    nodes, header = modlist.read_modlist(list_path)               # [2]
    scan.populate(nodes, mods_dir, progress=progress)             # [3] [5]
    masters.populate(nodes, mods_dir)

    cache = nexus.NexusCache(os.path.join(cache_dir, "nexus_cache.json"))
    ids = [n.nexus_id for n in nodes if n.nexus_id]
    if resolver is not None:                                      # [4]
        categories, nexus_report = resolver.resolve(ids)
        category_names = dict(cache.category_names)
    else:
        categories, category_names, nexus_report = nexus.resolve(
            ids, cache, api_key, domain, client=client)
    # MO2 keeps the same id -> name table on disk, so the half of the tiering
    # that needs it works with no key and no network at all.
    if not category_names:
        category_names = nexus.read_local_categories(mo2_root)
        if category_names:
            nexus_report += " (category names from nexuscatmap.dat)"
    tiers.apply(nodes, categories, category_names,
                nexus.read_mo2_categories(mo2_root),
                getattr(decisions, "categories", None) if decisions else None)

    # The requirement lists come off the v2 GraphQL API, which needs no key
    # and bills against a different quota, so this runs whether or not the
    # tiering above found a way to authenticate. Offline it is a no-op.
    needs: dict = {}
    req_cache = requirements.RequirementCache(
        os.path.join(cache_dir, "nexus_requirements.json"))
    game = requirements.game_id(domain, client=client)
    if game:
        needs, req_report = requirements.fetch(ids, req_cache, game,
                                              progress, client=client)
    else:
        needs, req_report = req_cache.needs, "requirements offline ({} cached)".format(
            len(req_cache.needs))
    nexus_report = "{}; {}".format(nexus_report, req_report)
    # Whatever was fetched is worth keeping for the next run, and for
    # whichever plugin asks about the same mods next.
    nexus_api.finish(client)

    movable = [n for n in dag.sortable(nodes) if n.enabled]
    disabled = [n for n in dag.sortable(nodes) if not n.enabled]
    edges = dag.build_edges(movable, needs)                       # [6]
    if decisions is None:
        decisions = decisions_mod.Decisions(
            os.path.join(cache_dir, "user_rules.json"))
    edges, dropped = dag.acyclic_edges(movable, edges, decisions)
    ordered = dag.topological_sort(movable, edges,            # [7]
                                   decisions.pins, decisions.freezes,
                                   decisions.freezes_below)

    # A disabled mod overrides nothing, so it has no place in the graph. It
    # keeps its own slot rather than being swept to one end, because the user
    # parked it there and will want to find it again.
    final_flow = _reassemble_disabled(dag.sortable(nodes), ordered, disabled)
    final = _reassemble(nodes, final_flow)

    was = {n.name: n.original_index for n in nodes}
    moved = [(n.name, was[n.name], i) for i, n in enumerate(final)
             if was[n.name] != i]
    report = "{}; {} edges ({} conflicts settled); {} of {} mods move".format(
        nexus_report, len(edges), len(dropped), len(moved), len(movable))
    # A pair the user has already ruled on is settled, not outstanding.
    unresolved = [e for e in dropped
                  if not decisions.known(e.parent, e.child)]
    return SortResult(final, header, moved, edges, dropped,
                      shadow.find(final, tiers.TIER_OUTPUT),
                      duplicates.find(final, tiers.TIER_OUTPUT),
                      uncategorised.find(final, tiers.TIER_OUTPUT), unresolved,
                      dag.unhonoured_freezes(ordered, decisions.freezes,
                                             decisions.freezes_below),
                      report)


def _reassemble_disabled(sortable_nodes, ordered, disabled):
    """Enabled mods take the enabled slots; disabled ones stay put."""
    if not disabled:
        return list(ordered)
    parked = {n.original_index: n for n in disabled}
    out: list = []
    flow = iter(ordered)
    for node in sortable_nodes:
        out.append(parked[node.original_index]
                   if node.original_index in parked else next(flow))
    return out


def apply_result(mo2_root: str, profile: str, result: SortResult,
                 label: str = "sort") -> str | None:
    path = os.path.join(mo2_root, "profiles", profile, "modlist.txt")
    return modlist.write_modlist(path, result.nodes, result.header,
                                 label=label)
