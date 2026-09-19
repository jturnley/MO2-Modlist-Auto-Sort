"""Component C's half of the contract: category id -> functional tier.

The spec's six tiers are an override hierarchy, not a taxonomy: a mod sits in
a tier because of what it is entitled to overwrite, so Tier 0 holds the things
nothing may sit on top of and Tier 5 holds the things that exist only to sit
on top of something else.

    0  engine fixes, script extenders, core frameworks
    1  user interface, fonts, HUD
    2  skeletons, mesh frameworks, base bodies
    3  worldspace, weather, environment, large textures
    4  item replacers, city and location edits, NPC visuals
    5  compatibility patches

Skyrim Special Edition's Nexus category ids are below.  A category the game
does not define, or a mod with no id at all, falls through to the file-tree
heuristic - that is the offline resilience rule, and it is also what happens
for every mod installed from outside Nexus.
"""

from __future__ import annotations

from . import scan

import os

DEFAULT_TIER = 3

# A sixth tier, past the end of the specified 0-5. Generated output - the
# folders a patcher writes after reading the whole list - is not a kind of
# mod at all: it is a snapshot of everything above it, so anything that
# overwrote it would be serving assets built from a list that no longer
# matches. Tier 5 is not enough, because patches are tier 5 too and a patch
# has no business sitting below the output built from it.
TIER_OUTPUT = 6

# Category *names*, not ids. The ids are per-game and not guessable: on
# Skyrim Special Edition 54 is Armour and 51 is Animation, while the numbers
# that look right from other Bethesda titles (19, 54 for animation, 60 for
# combat) belong to entirely different categories here. The game endpoint
# hands back id -> name, so the durable half of the mapping is the name, and
# this table then works unchanged on any game domain.
CATEGORY_NAME_TIERS: dict[str, int] = {
    # --- Tier 0: it runs the game -------------------------------------
    "bug fixes": 0,
    "utilities": 0,
    "modders resources": 0,
    "vr": 0,
    # --- Tier 1: it draws the interface -------------------------------
    "user interface": 1,
    "save games": 1,
    # --- Tier 2: it defines the body ----------------------------------
    "body, face, and hair": 2,
    "animation": 2,
    "races, classes, and birthsigns": 2,
    # --- Tier 3: it defines the world ---------------------------------
    "models and textures": 3,
    "visuals and graphics": 3,
    "environmental": 3,
    "audio": 3,
    "overhauls": 3,
    "gameplay": 3,
    "immersion": 3,
    "skills and leveling": 3,
    "magic - gameplay": 3,
    "combat": 3,
    "stealth": 3,
    "guilds/factions": 3,
    "alchemy": 3,
    "miscellaneous": 3,
    # --- Tier 4: it replaces or adds specific things ------------------
    "armour": 4,
    "armour - shields": 4,
    "weapons": 4,
    "weapons and armour": 4,
    "clothing and accessories": 4,
    "items and objects - player": 4,
    "items and objects - world": 4,
    "creatures and mounts": 4,
    "npc": 4,
    "followers & companions": 4,
    "followers & companions - creatures": 4,
    "quests and adventures": 4,
    "collectables, treasure hunts, and puzzles": 4,
    "player homes": 4,
    "buildings": 4,
    "cities, towns, villages, and hamlets": 4,
    "dungeons": 4,
    "locations -  new": 4,
    "locations - new": 4,
    "locations - vanilla": 4,
    "magic - spells & enchantments": 4,
    "presets - enb and reshade": 4,
    "crafting": 4,
    "shouts": 4,
    "cheats and god items": 4,
    # --- Tier 5: it exists to reconcile two other mods ----------------
    "patches": 5,
}


def tier_for_category(category_id: int | None,
                      category_names: dict[int, str] | None) -> int | None:
    """Tier for one Nexus category id, or None when it cannot be placed.

    A category the table does not list is not an error - it means the tiering
    falls through to the file tree, which is the same path a mod with no
    Nexus id at all takes.
    """
    if category_id is None or not category_names:
        return None
    name = (category_names.get(category_id) or "").strip().lower()
    return CATEGORY_NAME_TIERS.get(name)


# Words in a mod's own name that settle the tier without any metadata at all.
# The name is the author stating what they built, and for these six shapes it
# is more reliable than a category picked from a dropdown.
NAME_TIERS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (TIER_OUTPUT, ("_output", "output_", "pg_output", "pgpatcher",
                   "dyndolod_output", "texgen_output", "occlusion output",
                   "synthesis output", "bashed patch", "smashed patch",
                   "nemesis_output", "pandora_output", "autoblend_output")),
    (0, ("skse", "address library", "engine fixes", "crash log",
         "papyrusutil", "po3_", "powerofthree", "spell perk item distributor",
         "base object swapper", "keyword item distributor", "dyndolod resources",
         "unofficial skyrim special edition patch", ".net script framework")),
    (1, ("skyui", "moreHUD".lower(), "font", "interface", "menu",
         "widget", "hud")),
    (2, ("xp32", "skeleton", "cbbe", "bhunp", "unp ", "himbo", "3ba",
         "bodyslide", "racemenu", "faster hdt", "hdt-smp")),
    (5, ("patch", "patches", "compatibility", "hotfix")),
)


def _heuristic_tier(node) -> tuple[int, str]:
    """Tier from the file tree, for mods with no usable category.

    Deliberately crude, and ordered by how much a hit actually proves.  A DLL
    is an SKSE plugin and nothing else, so it settles the question; textures
    only suggest a visual mod, so they are consulted last.
    """
    files = node.file_manifest
    if not files:
        return DEFAULT_TIER, "no files scanned"
    exts = {os.path.splitext(f)[1] for f in files}
    dirs = {f.split("/", 1)[0] for f in files if "/" in f}

    if ".dll" in exts:
        return 0, "ships a DLL, so it is a script extender plugin"
    if ".hkx" in exts and any("skeleton" in f for f in files):
        return 2, "ships a skeleton"
    if dirs & {"interface", "fonts"} or exts & {".swf", ".fontconfig"}:
        return 1, "writes to interface/ or fonts/"
    if ".hkx" in exts:
        return 2, "ships animations"
    plugin_count = sum(1 for f in files if f.endswith(PLUGIN_EXTS_LOWER))
    if plugin_count and len(files) <= 4:
        # A mod that is only a plugin, with no assets of its own, is almost
        # always a patch: it has nothing to contribute but record edits.
        return 5, "a plugin and nothing else, so it is a patch"
    if ".dds" in exts or ".nif" in exts:
        return 3, "ships meshes or textures"
    return DEFAULT_TIER, "nothing decisive in the file tree"


PLUGIN_EXTS_LOWER = (".esp", ".esm", ".esl")


def assign(node, category_id: int | None,
           category_names: dict[int, str] | None = None,
           mo2_category_names: dict[int, str] | None = None
           ) -> tuple[int, str]:
    """(tier, why) for one node, in order of how much the evidence proves."""
    lname = node.name.lower()
    for tier, needles in NAME_TIERS:
        for needle in needles:
            if needle in lname:
                return tier, "name contains '{}'".format(needle)
    tier = tier_for_category(category_id, category_names)
    if tier is not None:
        return tier, "Nexus category '{}'".format(category_names[category_id])
    # A mod installed from a local archive has no Nexus id, but MO2 still
    # files it under a category - that is what the left pane's Category
    # column shows - and a category the user can see is better evidence than
    # a guess from the file tree. Without this, "High Poly Head SE" (modid=0)
    # was tiered on its meshes while its own UV fix was tiered on the
    # category both of them are filed under, and the fix sorted above it.
    for cat in getattr(node, "mo2_categories", ()) or ():
        name = (mo2_category_names or {}).get(cat)
        if name and name.lower() in CATEGORY_NAME_TIERS:
            return (CATEGORY_NAME_TIERS[name.lower()],
                    "MO2 category '{}'".format(name))
    return _heuristic_tier(node)


def apply(nodes, categories: dict[int, int],
          category_names: dict[int, str] | None = None,
          mo2_category_names: dict[int, str] | None = None,
          given: dict[str, str] | None = None) -> None:
    """Set ``tier`` and ``tier_reason`` on every sortable node.

    ``categories`` is {nexus mod id: category id}, whatever Component C
    managed to resolve; a missing id is not an error, it just means the
    heuristics decide.
    """
    for node in nodes:
        if node.is_separator or node.is_unmanaged:
            continue
        cat = categories.get(node.nexus_id) if node.nexus_id else None
        node.tier, node.tier_reason = assign(node, cat, category_names,
                                             mo2_category_names)
        name = (category_names or {}).get(cat) if cat else None
        if name is None:
            for mo2_cat in getattr(node, "mo2_categories", ()) or ():
                name = (mo2_category_names or {}).get(mo2_cat)
                if name:
                    break
        # A category the user typed in outranks everything: they are
        # looking at the mod, and the alternative here was a guess made
        # from the file tree.
        chosen = (given or {}).get(node.name, "").strip()
        if chosen:
            settled = CATEGORY_NAME_TIERS.get(chosen.lower())
            if settled is not None:
                node.tier = settled
                node.tier_reason = "category you gave it: '{}'".format(chosen)
            name = chosen
        node.category_name = name or ""
        node.is_tool = scan.looks_like_tool(
            node.category_name, node.file_manifest, node.name)
        node.subtier = surface_rank(node.name, node.category_name)
        if node.subtier:
            node.tier_reason += " ({})".format(
                "PBR, so below the rest" if node.subtier == 3
                else "parallax/complex, so below plain textures")


# Within Models and Textures, what a mod is decides which way round a pair
# of texture mods goes, and nothing in a file tree says it.  A PBR set has
# to sit below a parallax or complex-material one, which has to sit below a
# plain retexture, or the lower one is rendered with the wrong shader data
# and the surface comes out flat or wrong.
#
# Only inside that category.  A mod filed under Armour whose name mentions
# parallax is being described, not positioned; the category is what says
# this mod is competing for the same surfaces as the others around it.
TEXTURE_CATEGORY = "models and textures"
SURFACE_RANKS = (
    (3, ("pbr",)),
    (2, ("parallax", "complex", "enb")),
)


def surface_rank(name: str, category_name: str | None) -> int:
    """How late a texture mod goes within its tier: PBR last, plain first."""
    if (category_name or "").lower() != TEXTURE_CATEGORY:
        return 0
    # Whole words only, so "Complexion" and "Benched" are not read as claims.
    words = set()
    word = []
    for ch in name.lower():
        if ch.isalnum():
            word.append(ch)
        elif word:
            words.add("".join(word))
            word = []
    if word:
        words.add("".join(word))
    for rank, markers in SURFACE_RANKS:
        if words & set(markers):
            return rank
    return 0


def words_in(name: str) -> set:
    """The whole words of a mod name, lowercased."""
    out, word = set(), []
    for ch in (name or "").lower():
        if ch.isalnum():
            word.append(ch)
        elif word:
            out.add("".join(word))
            word = []
    if word:
        out.add("".join(word))
    return out


# The shader framework that draws PBR and parallax surfaces. Everything
# using those shaders has to load after it, whatever category it is filed
# under - "Alternative Armors Redone PBR" is filed as Armour and is still
# drawn by the same code as a landscape set.
SHADER_FRAMEWORKS = ("community shaders",)

# ENB is deliberately not here. It is the alternative to Community Shaders
# rather than a thing that sits beneath it, so ordering one against the
# other would be inventing a relationship that does not exist.
SHADER_USERS = ("pbr", "parallax")


def is_shader_framework(name: str) -> bool:
    """True only for the framework itself, not for its add-ons.

    "Skylighting - Community Shaders" is a feature that plugs into it and
    has nothing to say about where a texture set loads.
    """
    return (name or "").strip().lower() in SHADER_FRAMEWORKS


def uses_shaders(name: str) -> bool:
    low = (name or "").lower()
    return bool(words_in(name) & set(SHADER_USERS)) or "complex material" in low
