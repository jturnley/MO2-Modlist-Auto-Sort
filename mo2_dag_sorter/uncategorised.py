"""Mods nothing could put a category on.

Tiering asks three sources in turn: the Nexus category, MO2's own category,
and failing both, the shape of the file tree.  The third is a guess.  It is
a decent guess - a mod shipping nothing but meshes and textures really is
usually a texture mod - but it is made from what a mod *contains* rather
than what it *is*, and it cannot tell an armour replacer from the retexture
of one.

Two ways to arrive here, and they want different things said to the user:

    not from Nexus      no mod id at all, so there was never a category to
                        look up.  Installed from LoversLab, Discord, a
                        Google Drive link, or built by hand.

    category unknown    a mod id is present and the lookup came back with
                        nothing, or with a category name the tier table
                        does not list.  Usually a category added to Nexus
                        since the local nexuscatmap.dat was written.

Generated output and tools are not asked about: neither is sorted on its
category and neither has one to give.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Unknown:
    name: str
    nexus_id: int | None
    tier: int
    guess: str                    # the tier_reason the file tree settled on

    @property
    def off_nexus(self) -> bool:
        return not self.nexus_id

    @property
    def why(self) -> str:
        if self.off_nexus:
            return "not from Nexus, so there is no category to look up"
        return "Nexus category {} did not resolve".format(self.nexus_id)

    @property
    def headline(self) -> str:
        return "{} - {}; placed by guess: {}".format(
            self.name, self.why, self.guess)


def find(nodes, output_tier: int = 6) -> list[Unknown]:
    """Every enabled mod placed by the file tree rather than a category."""
    out: list[Unknown] = []
    for node in nodes:
        if (node.is_separator or node.is_unmanaged or not node.enabled
                or node.is_tool or node.tier >= output_tier):
            continue
        if getattr(node, "category_name", ""):
            continue
        if not node.file_manifest:
            continue
        out.append(Unknown(node.name, node.nexus_id, node.tier,
                           node.tier_reason))
    return out
