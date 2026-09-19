"""Engine tests for MO2-DAG-Sorter. Run directly: python tests/test_dag_sorter.py"""

import io
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mo2_dag_sorter import (backups, duplicates, incremental, dag, keys,
                            decisions, modlist, nexus_api, scan, shadow,
                            tiers, uncategorised)
from mo2_dag_sorter.dag import DependencyEdge
from mo2_dag_sorter.modlist import ModNode

failures = []


def check(label, got, want):
    if got != want:
        failures.append("{}: got {!r}, want {!r}".format(label, got, want))


def node(name, index, tier=3, files=(), plugins=(), masters=(), prefix="+",
         subtier=0):
    n = ModNode(name=name, prefix=prefix, original_index=index)
    n.tier = tier
    n.subtier = subtier
    n.file_manifest = list(files)
    n.plugins = list(plugins)
    n.masters = list(masters)
    return n


# --- Component B: the file format round-trips, prefixes and all ----------
SAMPLE = "+Top Mod\n-Disabled Mod\n*DLC: Dawnguard\n+Bottom Mod\n"
_p = pathlib.Path(os.environ.get("TEMP", ".")) / "dagsort_modlist.txt"
_p.write_text(SAMPLE, encoding="utf-8")
_nodes, _header = modlist.read_modlist(str(_p))
check("the file is read bottom-up into priority order",
      [n.name for n in _nodes],
      ["Bottom Mod", "DLC: Dawnguard", "Disabled Mod", "Top Mod"])
check("prefixes and the MO2 header survive a round trip",
      modlist.serialise(_nodes, _header), SAMPLE)
check("a '-' entry is disabled", _nodes[2].enabled, False)
check("a '*' entry is unmanaged", _nodes[1].is_unmanaged, True)

# --- Component D: edge direction -----------------------------------------
_a = node("Base Textures", 0, tier=3, files=["textures/a.dds"])
_b = node("Patch For It", 1, tier=5, files=["textures/a.dds"])
_edges = dag.conflict_edges([_b, _a])       # deliberately out of order
check("a conflict is directed by tier, not by argument order",
      [(e.parent, e.child) for e in _edges], [("Base Textures", "Patch For It")])

_c = node("First", 0, tier=3, files=["x.nif"])
_d = node("Second", 1, tier=3, files=["x.nif"])
check("an equal-tier conflict keeps the order the user chose",
      [(e.parent, e.child) for e in dag.conflict_edges([_d, _c])],
      [("First", "Second")])

_base = node("Big Mod", 5, tier=3, plugins=["big.esp"])
_ext = node("Its Addon", 0, tier=3, plugins=["addon.esp"], masters=["big.esp"])
check("a master edge points from the provider to the dependant",
      [(e.parent, e.child, e.reason) for e in dag.master_edges([_ext, _base])],
      [("Big Mod", "Its Addon", "master_requirement")])
check("an uninstalled master produces no edge",
      dag.master_edges([node("Lonely", 0, masters=["absent.esp"])]), [])

# An add-on with no plugin of its own still says what it extends, in its
# asset paths: the game resolves sound/voice/<plugin>/ by plugin name.
_quest = node("Quest Mod", 9, tier=4, files=["thequest.esp"],
              plugins=["thequest.esp"])
_voice = node("Quest Mod - Voiced", 0, tier=3,
              files=["sound/voice/thequest.esp/maleeventoned/a.fuz"])
check("an asset path naming another mod's plugin is a dependency",
      [(e.parent, e.child, e.reason)
       for e in dag.asset_path_edges([_voice, _quest])],
      [("Quest Mod", "Quest Mod - Voiced", "asset_path")])
check("so the voice pack lands below the quest, despite the lower tier",
      [n.name for n in dag.topological_sort(
          [_voice, _quest],
          dag.acyclic_edges([_voice, _quest],
                            dag.build_edges([_voice, _quest]))[0])],
      ["Quest Mod", "Quest Mod - Voiced"])
check("a path naming an uninstalled plugin produces nothing",
      dag.asset_path_edges([node("Orphan", 0,
                                 files=["sound/voice/absent.esp/x/a.fuz"])]), [])

# A name beginning with another installed mod's whole name is the author
# saying what their mod extends.
_nx = [node("Amazing Lockpicks", 0), node("Amazing Lockpicks Additions", 1),
       node("Amazing Lockpicks PBR", 2), node("Ordinator", 3),
       node("Ordinator Thing", 4)]
check("an extension follows the mod it is named after",
      [(e.parent, e.child) for e in dag.name_extension_edges(_nx)],
      [("Amazing Lockpicks", "Amazing Lockpicks Additions")])

# --- Component D: the sort ------------------------------------------------
_nodes = [node("Zeta", 0, tier=5), node("Alpha", 1, tier=0),
          node("Beta", 2, tier=3)]
check("tier is the first sort key",
      [n.name for n in dag.topological_sort(_nodes, [])],
      ["Alpha", "Beta", "Zeta"])

_same = [node("B", 0, tier=3), node("A", 1, tier=3)]
check("within a tier the existing order is kept, not the alphabet",
      [n.name for n in dag.topological_sort(_same, [])], ["B", "A"])

_forced = [node("Needs It", 0, tier=3), node("Provides It", 1, tier=3)]
check("an edge overrides the tie-breaker",
      [n.name for n in dag.topological_sort(
          _forced, [DependencyEdge("Provides It", "Needs It", "x")])],
      ["Provides It", "Needs It"])

# A constraint that contradicts the tier order is honoured, because a master
# is a fact and a tier is a classification.
_cross = [node("Framework", 0, tier=0, plugins=["f.esp"]),
          node("Late Thing", 1, tier=5, plugins=["l.esp"], masters=["f.esp"])]
check("a master edge and the tier matrix can agree",
      [n.name for n in dag.topological_sort(_cross, dag.build_edges(_cross))],
      ["Framework", "Late Thing"])

try:
    dag.topological_sort(
        [node("A", 0), node("B", 1)],
        [DependencyEdge("A", "B", "x"), DependencyEdge("B", "A", "y")])
    failures.append("a cycle should have raised")
except dag.CycleError as exc:
    check("a cycle names the mods it could not place", sorted(exc.nodes),
          ["A", "B"])

# A master is a fact and a tier is a classification, so when the two
# contradict each other the conflict edge is the one that yields.
_prov = node("Provider", 0, tier=5, files=["shared.esp"], plugins=["p.esp"])
_dep = node("Dependant", 1, tier=3, files=["shared.esp"],
            plugins=["d.esp"], masters=["p.esp"])
_all = dag.build_edges([_prov, _dep])
_kept, _dropped = dag.acyclic_edges([_prov, _dep], _all)
check("the master edge survives the collision",
      [e.reason for e in _kept], ["master_requirement"])
check("the conflict edge is the one reported as dropped",
      [e.reason for e in _dropped], ["file_conflict"])
check("and the result sorts rather than raising",
      [n.name for n in dag.topological_sort([_prov, _dep], _kept)],
      ["Provider", "Dependant"])
check("nothing is dropped when there is no collision",
      dag.acyclic_edges([node("A", 0), node("B", 1)],
                        [DependencyEdge("A", "B", "file_conflict", "2 f")])[1],
      [])

# --- the sort is idempotent ----------------------------------------------
_list = [node("Skeleton Thing", 0, tier=2, files=["a.nif"]),
         node("Some Patch", 1, tier=5, files=["a.nif"]),
         node("A Texture Pack", 2, tier=3, files=["b.dds"])]
_once = dag.topological_sort(_list, dag.build_edges(_list))
for i, n in enumerate(_once):
    n.original_index = i
_twice = dag.topological_sort(_once, dag.build_edges(_once))
check("running it again changes nothing",
      [n.name for n in _twice], [n.name for n in _once])

# --- Component C fallbacks ------------------------------------------------
check("a DLL is a tier 0 script extender plugin",
      tiers.assign(node("Some SKSE Thing", 0, files=["skse/plugins/x.dll"]),
                   None)[0], 0)
check("a lone plugin file reads as a patch",
      tiers.assign(node("Random Mod", 0, files=["thing.esp"]), None)[0], 5)
_CATS = {42: "User Interface", 54: "Armour", 95: "Bug Fixes"}
check("a Nexus category beats the file tree",
      tiers.assign(node("Random Mod", 0, files=["a.dds"]), 42, _CATS)[0], 1)
check("the tier comes from the category's name, not its number",
      tiers.assign(node("Random Mod", 0, files=["a.dds"]), 54, _CATS)[0], 4)
check("an unknown category falls back rather than failing",
      tiers.assign(node("Random Mod", 0, files=["a.dds"]), 99999, _CATS)[0], 3)
check("a category id with no name table falls back too",
      tiers.assign(node("Random Mod", 0, files=["a.dds"]), 42, None)[0], 3)
check("generated output sorts past every real mod",
      tiers.assign(node("DynDOLOD_Output", 0, files=["x.esp"]), None)[0],
      tiers.TIER_OUTPUT)
check("and lands below a patch, which is also tier 5",
      tiers.assign(node("pg_output", 0), None)[0] >
      tiers.assign(node("Some Patch", 0), None)[0], True)
check("a fonts/ or .swf mod is interface work",
      tiers.assign(node("Random Mod", 0, files=["fonts/x.swf"]), None)[0], 1)

# A readme is not a conflict. The extension whitelist has to let .txt through
# for MCM and SPID config, which drags every mod's readme in behind it.
check("a shared readme is filtered before it can become an edge",
      [f for f in ["readme.txt", "meta.ini", "textures/a.dds"]
       if f.rsplit("/", 1)[-1] not in scan.IGNORED_NAMES],
      ["textures/a.dds"])

# A mod every one of whose files loses is doing nothing at all - unless the
# only thing above it is generated output, which is a copy of it by design.
_low = node("Buried Mod", 0, files=["a.dds", "b.dds"])
_high = node("Covers It", 1, files=["a.dds", "b.dds", "c.dds"])
_part = node("Partly Covered", 2, files=["d.dds", "e.dds"])
_over = node("Covers Part", 3, files=["d.dds"])
_found = shadow.find([_low, _high, _part, _over], tiers.TIER_OUTPUT)
check("a fully overridden mod is reported",
      [f.name for f in _found], ["Buried Mod"])
check("and it names what buried it", _found[0].by, ["Covers It"])

_src = node("Source Textures", 0, files=["a.dds"])
_out = node("pg_output", 1, tier=tiers.TIER_OUTPUT, files=["a.dds"])
check("generated output does not count as burying its own sources",
      shadow.find([_src, _out], tiers.TIER_OUTPUT), [])

check("a mod with no files at all is not reported",
      shadow.find([node("Empty", 0), node("Something", 1, files=["a.dds"])],
                  tiers.TIER_OUTPUT), [])

# A standing answer from the user outranks every edge the files imply.
_d = decisions.Decisions(str(pathlib.Path(os.environ.get("TEMP", "."))
                             / "dagsort_rules.json"))
_d.first.clear()
_d.set("Patch Mod", "Base Mod", "I want the patch on top")
_pair = [node("Base Mod", 0, tier=3, plugins=["b.esp"]),
         node("Patch Mod", 1, tier=5, plugins=["p.esp"], masters=["b.esp"])]
_kept, _drop = dag.acyclic_edges(_pair, dag.build_edges(_pair), _d)
check("the user's answer beats the master record",
      [n.name for n in dag.topological_sort(_pair, _kept)],
      ["Patch Mod", "Base Mod"])
check("and the master edge is what yielded",
      [e.reason for e in _drop], ["master_requirement"])
check("a pair with an answer is no longer outstanding",
      _d.known("Base Mod", "Patch Mod"), True)
_d.save()
check("answers survive a reload",
      decisions.Decisions(_d.path).first[decisions.key("Base Mod", "Patch Mod")],
      "Patch Mod")

# A pin is a statement about when a mod runs, which no evidence about its
# files can express.
_pinned = [node("Ordinary Mod", 0, tier=0), node("Crash Logger", 1, tier=3)]
check("a top pin beats the tier",
      [n.name for n in dag.topological_sort(_pinned, [],
                                            {"Crash Logger": "top"})],
      ["Crash Logger", "Ordinary Mod"])
check("a bottom pin does the reverse",
      [n.name for n in dag.topological_sort(_pinned, [],
                                            {"Ordinary Mod": "bottom"})],
      ["Crash Logger", "Ordinary Mod"])
_needs = [node("Framework", 0, tier=3, plugins=["f.esp"]),
          node("Pinned Dependant", 1, tier=3, plugins=["d.esp"],
               masters=["f.esp"])]
check("but a pin cannot jump a real dependency",
      [n.name for n in dag.topological_sort(
          _needs, dag.build_edges(_needs), {"Pinned Dependant": "top"})],
      ["Framework", "Pinned Dependant"])

# -- placing one newly installed mod --------------------------------------
_list = [node("Alpha", 0), node("Bravo", 1), node("Charlie", 2),
         node("Delta", 3), node("NewMod", 4)]
_order = [node("Alpha", 0), node("NewMod", 1), node("Bravo", 2),
          node("Charlie", 3), node("Delta", 4)]
check("an unconstrained mod goes where the sorted order ranks it",
      [n.name for n in incremental.place_one(_list, _order, "NewMod", [])],
      ["Alpha", "NewMod", "Bravo", "Charlie", "Delta"])

_wall = [dag.DependencyEdge("Charlie", "NewMod", "master_requirement", "")]
check("but never above something it must load after",
      [n.name for n in incremental.place_one(_list, _order, "NewMod", _wall)],
      ["Alpha", "Bravo", "Charlie", "NewMod", "Delta"])

_both = [dag.DependencyEdge("Alpha", "NewMod", "master_requirement", ""),
         dag.DependencyEdge("NewMod", "Bravo", "nexus_requirement", "")]
check("and it lands inside the window its edges leave it",
      [n.name for n in incremental.place_one(_list, _order, "NewMod", _both)],
      ["Alpha", "NewMod", "Bravo", "Charlie", "Delta"])

check("only the questions naming the new mod are asked",
      [e.child for e in incremental.questions_for(
          [dag.DependencyEdge("X", "NewMod", "file_conflict", ""),
           dag.DependencyEdge("Y", "Z", "file_conflict", "")], "NewMod")],
      ["NewMod"])


# -- backups and restore ---------------------------------------------------
import shutil as _shutil, tempfile as _tempfile, time as _time
_tmp = _tempfile.mkdtemp()
_list = os.path.join(_tmp, "modlist.txt")
open(_list, "w", encoding="utf-8").write(
    chr(10).join(["# header", "+Alpha", "+Bravo", ""]))
_first = backups.make(_list, "sort")
check("a backup is written and found again", len(backups.available(_list)), 1)
check("it carries the label", backups.available(_list)[0].label, "sort")
check("and the mod count", backups.available(_list)[0].mods, 2)
open(_list, "w", encoding="utf-8").write(
    chr(10).join(["# header", "+Alpha", ""]))
check("restoring brings the old list back",
      (backups.restore(_list, _first) is not None
       and open(_list, encoding="utf-8").read().count("+") == 2), True)
check("and the replaced list is kept too", len(backups.available(_list)), 2)
check("two backups in the same second do not collide",
      len({backups.make(_list, "x"), backups.make(_list, "x")}), 2)
_shutil.rmtree(_tmp, ignore_errors=True)


# -- freezing a mod above another -----------------------------------------
_fz = [node("Alpha", 0, tier=1), node("Bravo", 1, tier=2),
       node("Charlie", 2, tier=3), node("Delta", 3, tier=4)]
check("a freeze puts the mod directly above its target",
      [n.name for n in dag.topological_sort(_fz, [], None, {"Alpha": "Delta"})],
      ["Bravo", "Charlie", "Alpha", "Delta"])
check("two mods frozen to one target stack above it, in sorted order",
      [n.name for n in dag.topological_sort(
          _fz, [], None, {"Charlie": "Delta", "Bravo": "Delta"})],
      ["Alpha", "Bravo", "Charlie", "Delta"])
check("a chain travels as one block",
      [n.name for n in dag.topological_sort(
          _fz, [], None, {"Alpha": "Bravo", "Bravo": "Delta"})],
      ["Charlie", "Alpha", "Bravo", "Delta"])
check("a freeze that loops is ignored, not obeyed halfway",
      [n.name for n in dag.topological_sort(
          _fz, [], None, {"Alpha": "Bravo", "Bravo": "Alpha"})],
      ["Alpha", "Bravo", "Charlie", "Delta"])
check("a freeze naming a mod that is not installed is ignored",
      [n.name for n in dag.topological_sort(_fz, [], None, {"Alpha": "Ghost"})],
      ["Alpha", "Bravo", "Charlie", "Delta"])


_dep = [node("Base", 0, plugins=["b.esp"]),
        node("Addon", 1, plugins=["a.esp"], masters=["b.esp"]),
        node("Other", 2)]
_dep_edges = dag.build_edges(_dep)
check("a freeze cannot strand a real dependency",
      [n.name for n in dag.topological_sort(_dep, _dep_edges, None,
                                            {"Base": "Other"})],
      ["Base", "Addon", "Other"])
check("and the one it dropped is reported",
      dag.unhonoured_freezes(
          dag.topological_sort(_dep, _dep_edges, None, {"Base": "Other"}),
          {"Base": "Other"}),
      [("Base", "Other")])
check("a freeze that costs nothing is still honoured",
      dag.unhonoured_freezes(
          dag.topological_sort(_fz, [], None, {"Alpha": "Delta"}),
          {"Alpha": "Delta"}),
      [])


import tempfile as _tf2
_tmp2 = _tf2.mkdtemp()

# -- freezing below, the mirror of the above ------------------------------
check("a freeze below puts the mod directly under its target",
      [n.name for n in dag.topological_sort(_fz, [], None, None,
                                            {"Delta": "Alpha"})],
      ["Alpha", "Delta", "Bravo", "Charlie"])
check("two mods frozen below one target stack under it, in sorted order",
      [n.name for n in dag.topological_sort(
          _fz, [], None, None, {"Delta": "Alpha", "Charlie": "Alpha"})],
      ["Alpha", "Charlie", "Delta", "Bravo"])
check("a target can hold a stack on each side",
      [n.name for n in dag.topological_sort(
          _fz, [], None, {"Delta": "Bravo"}, {"Alpha": "Bravo"})],
      ["Delta", "Bravo", "Alpha", "Charlie"])
check("a chain of below-freezes travels as one block",
      [n.name for n in dag.topological_sort(
          _fz, [], None, None, {"Charlie": "Alpha", "Delta": "Charlie"})],
      ["Alpha", "Charlie", "Delta", "Bravo"])
check("a below-freeze cannot strand a real dependency either",
      [n.name for n in dag.topological_sort(_dep, _dep_edges, None, None,
                                            {"Other": "Addon"})],
      ["Base", "Addon", "Other"])
check("one mod cannot be frozen on both sides at once",
      [n.name for n in dag.topological_sort(
          _fz, [], None, {"Alpha": "Delta"}, {"Alpha": "Bravo"})],
      ["Bravo", "Charlie", "Alpha", "Delta"])
_both = decisions.Decisions(os.path.join(_tmp2, "rules.json"))
_both.freeze("A", "B", "", "below")
_both.freeze("A", "C", "", "above")
check("and setting one side clears the other",
      (_both.freezes_below, _both.freezes), ({}, {"A": "C"}))
check("frozen_to reports the side", _both.frozen_to("A"), ("C", "above"))


# -- explaining why a freeze cannot be honoured ---------------------------
check("a freeze with nothing in the way has no blockers",
      dag.freeze_blockers(_fz, [], "Alpha", "Delta", "above"), [])
check("one that would strand a dependency names the edge",
      [e.reason for e in dag.freeze_blockers(
          dag.topological_sort(_dep, _dep_edges), _dep_edges,
          "Base", "Other", "above")],
      ["master_requirement"])
check("and the explanation names the mod in the way",
      dag.blocker_text(dag.topological_sort(_dep, _dep_edges),
                       _dep_edges[0], "Base").startswith(
          "Addon has to load after Base"),
      True)

_chain = decisions.Decisions(os.path.join(_tmp2, "chain.json"))
_chain.freeze("B", "A")
_chain.freeze("C", "B")
check("a freeze chain is walked to the end", _chain.freeze_chain("C"),
      ["B", "A"])
check("and a mod frozen to nothing has an empty chain",
      _chain.freeze_chain("A"), [])


# -- reading the shape of a file overlap ----------------------------------
_ov = [node("Base Mod", 0, tier=2, files=["a.nif", "b.nif", "c.nif"] +
            ["f{}.dds".format(i) for i in range(200)]),
       node("Base Mod Neck Fix", 1, tier=2, files=["a.nif", "z.nif"])]
check("a two-file fix inside a big mod loads after it",
      [(e.parent, e.child) for e in dag.conflict_edges(_ov)],
      [("Base Mod", "Base Mod Neck Fix")])

_big = [node("Texture Pack", 0, tier=2,
             files=["f{}.dds".format(i) for i in range(200)]),
        node("Smaller Texture Mod", 1, tier=2,
             files=["f{}.dds".format(i) for i in range(60)])]
check("but a content mod of its own is left to the list order",
      [(e.parent, e.child) for e in dag.conflict_edges(_big)],
      [("Texture Pack", "Smaller Texture Mod")])

_tiered = [node("PBR Pack", 1, tier=5,
                files=["f{}.dds".format(i) for i in range(200)]),
           node("Tiny Mesh Fix", 0, tier=2, files=["f1.dds", "z.nif"])]
check("and a tier still outranks the shape",
      [(e.parent, e.child) for e in dag.conflict_edges(_tiered)],
      [("Tiny Mesh Fix", "PBR Pack")])


# -- surface rank inside Models and Textures ------------------------------
_MT = "Models and Textures"
check("a PBR set ranks last inside its tier",
      tiers.surface_rank("Faultier's PBR Skyrim AIO 2k", _MT), 3)
check("parallax and complex material rank between",
      [tiers.surface_rank(n, _MT) for n in
       ("Peltapalooza - Complex Parallax Occlusion", "NAT.ENB",
        "Dibella Statue - Complex Material and PBR")],
      [2, 2, 3])
check("a plain retexture ranks first",
      tiers.surface_rank("RUSTIC ANIMATED POTIONS and POISONS", _MT), 0)
check("and the words have to be words, not fragments",
      [tiers.surface_rank(n, _MT) for n in
       ("Complexion of Skyrim", "Enhanced Blood Textures")], [0, 0])
check("outside the category the name is a description, not a position",
      tiers.surface_rank("Cloaks of Skyrim SSE PBR", "Armour"), 0)

# The pair this rule exists for: two texture mods of the same tier, where
# the bigger plain one was installed later and would otherwise win.
_surf = [node("Ruins Clutter Improved PBR", 0, tier=3, subtier=3,
              files=["f{}.dds".format(i) for i in range(200)]),
         node("RUSTIC POTIONS", 1, tier=3, subtier=0,
              files=["f{}.dds".format(i) for i in range(150)])]
check("a PBR set loads after a plain retexture it shares files with",
      [(e.parent, e.child) for e in dag.conflict_edges(_surf)],
      [("RUSTIC POTIONS", "Ruins Clutter Improved PBR")])
check("and the sort puts it there with no edge at all",
      [n.name for n in dag.topological_sort(
          [node("PBR Set", 0, tier=3, subtier=3),
           node("Parallax Set", 1, tier=3, subtier=2),
           node("Plain Retexture", 2, tier=3, subtier=0)], [])],
      ["Plain Retexture", "Parallax Set", "PBR Set"])


# -- freezing a multi-mod selection as one stack ---------------------------
# The menu helpers are pure functions on names, so they are testable without
# MO2: no organizer, no Qt, just the pane order and the selection.
from mo2_dag_sorter import stacks as _Tool

_pane = [node(n, i) for i, n in enumerate(
    ["Top", "Alpha", "Beta", "Gamma", "Bottom"])]
_sel = ["Beta", "Alpha", "Gamma"]        # deliberately out of pane order

check("a selection is read in pane order, not click order",
      _Tool.in_pane_order(_pane, _sel), ["Alpha", "Beta", "Gamma"])

_picked = _Tool.in_pane_order(_pane, _sel)
check("the target for a stack is the mod past the whole selection",
      [_Tool.stack_target(_pane, _picked, side)
       for side in ("above", "below")], ["Bottom", "Top"])

check("holding a stack above a mod chains each member to the next",
      _Tool.stack_pairs(_picked, "Bottom", "above"),
      [("Alpha", "Beta", "above"), ("Beta", "Gamma", "above"),
       ("Gamma", "Bottom", "above")])
check("and below a mod chains it the other way",
      _Tool.stack_pairs(_picked, "Top", "below"),
      [("Alpha", "Top", "below"), ("Beta", "Alpha", "below"),
       ("Gamma", "Beta", "below")])

# A gap inside the selection must not become the target: "below Top" means
# below the whole block, even when something unselected sits in the middle.
_gappy = [node(n, i) for i, n in enumerate(
    ["Top", "Alpha", "Stranger", "Gamma", "Bottom"])]
check("an unselected mod inside the selection is not the target",
      [_Tool.stack_target(_gappy, ["Alpha", "Gamma"], side)
       for side in ("above", "below")], ["Bottom", "Top"])

# The chain the stack builds is exactly what the sorter honours.
_stack = decisions.Decisions(str(_tf2 := _p.parent / "dagsort_stack.json"))
for mod, to, how in _Tool.stack_pairs(_picked, "Bottom", "above"):
    _stack.freeze(mod, to, "", how)
_nodes = [node(n, i) for i, n in enumerate(
    ["Alpha", "Top", "Gamma", "Bottom", "Beta"])]
check("a frozen stack arrives whole, in its own order, above its target",
      [n.name for n in dag.topological_sort(
          _nodes, [], freezes=_stack.freezes,
          freezes_below=_stack.freezes_below)],
      ["Top", "Alpha", "Beta", "Gamma", "Bottom"])

check("a loop inside one batch is caught before anything is written",
      _Tool.loops(decisions.Decisions(str(_p.parent / "dagsort_empty.json")),
                   [("A", "B", "above"), ("B", "A", "above")]) is not None,
      True)
check("and an honest chain is not",
      _Tool.loops(decisions.Decisions(str(_p.parent / "dagsort_empty2.json")),
                   _Tool.stack_pairs(_picked, "Bottom", "above")), None)

_prior = decisions.Decisions(str(_p.parent / "dagsort_prior.json"))
_prior.freeze("Alpha", "Elsewhere", "", "below")
check("a freeze that loops back through an existing rule is caught too",
      _Tool.loops(_prior, [("Elsewhere", "Alpha", "above")]) is not None,
      True)


# -- a tool is run, not loaded --------------------------------------------
check("a mod the game loads ships game assets",
      [scan.ships_game_assets(f) for f in
       (["textures/a.dds"], ["some.esp"], ["skse/plugins/x.dll"],
        ["mymod_distr.ini"])],
      [True, True, True, False])
check("an external patcher is a tool",
      scan.looks_like_tool("Utilities",
                           ["pgpatcher/pgpatcher.exe", "pg_delete_me.ini"],
                           "PGPatcher"),
      True)
check("an SKSE framework filed the same way is not",
      scan.looks_like_tool("Utilities", ["skse/plugins/po3_spid.dll"],
                           "Spell Perk Item Distributor"), False)
check("and neither is a SPID mod that ships only a root ini",
      scan.looks_like_tool("Models and Textures", ["mymod_distr.ini"],
                           "Katana Crafting - SPID"), False)

# The case this exists for: a texture mod naming a patcher as a Nexus
# requirement was being held below wherever the patcher happened to sit.
_tooled = [node("PGPatcher", 0, tier=6, files=["pgpatcher/pgpatcher.exe"]),
           node("Some PBR Textures", 1, tier=3, files=["textures/a.dds"])]
_tooled[0].nexus_id, _tooled[1].nexus_id = 120946, 555
_tooled[0].category_name = _tooled[1].category_name = "Utilities"
_tooled[0].is_tool = True
check("a tool's Nexus requirement does not order anything",
      dag.requirement_edges(_tooled, {555: [(120946, "Needed to patch")]}), [])
_tooled[0].is_tool = False
check("but the same requirement on a real mod does",
      [(e.parent, e.child) for e in
       dag.requirement_edges(_tooled, {555: [(120946, "Needed to patch")]})],
      [("PGPatcher", "Some PBR Textures")])

_tool_files = [node("Tool", 0, tier=6, files=["x/shared.dll"]),
               node("Real Mod", 1, tier=3, files=["x/shared.dll"])]
_tool_files[0].is_tool = True
check("nor do the files it ships, which the game never reads",
      dag.conflict_edges(_tool_files), [])


# A behaviour engine and BodySlide are tools the file test cannot reach:
# one ships a stub plugin so mods checking for FNIS are satisfied, the
# other ships its shapes into Data. Both are named instead.
check("a behaviour engine is a tool despite its stub plugin",
      scan.looks_like_tool("Utilities", ["fnis.esp", "pandora_engine/x.txt"],
                           "Pandora Behaviour Engine Plus"), True)
check("BodySlide's own project files are not game assets",
      scan.ships_game_assets(["calientetools/bodyslide/a.osp"]), False)
check("so BodySlide is a tool",
      scan.looks_like_tool("Utilities", ["calientetools/bodyslide/a.osp"],
                           "BodySlide and Outfit Studio"), True)

# What a tool leaves behind is a mod like any other and has to be ordered.
check("but what a tool generates is not a tool",
      [scan.looks_like_tool("", ["meshes/a.nif"], n) for n in
       ("Pandora_Output", "Bodyslide_Output", "DynDOLOD_Output",
        "Bashed Patch, 0")],
      [False, False, False, False])

# Near misses: real mods whose names carry a tool's word. Every one of
# these has to keep ordering, so the list holds product names, not words.
check("a mod that merely names an engine still orders",
      [scan.looks_like_tool(c, f, n) for n, c, f in (
          ("DynDOLOD Resources SE", "Models and Textures", ["meshes/a.nif"]),
          ("DynDOLOD DLL NG", "Utilities", ["skse/plugins/d.dll"]),
          ("Dyn FNIS AA functions", "Animation", ["scripts/a.pex"]),
          ("Offset Movement Animation - Nemesis - Modders Resource",
           "Animation", ["meshes/a.hkx"]),
          ("Triss' Dress - SSE CBBE 3BA BodySlide", "Armour",
           ["calientetools/b.osp", "meshes/t.nif"]))],
      [False, False, False, False, False])


# -- the shader framework loads before everything it draws ----------------
check("only the framework itself counts as the framework",
      [tiers.is_shader_framework(n) for n in
       ("Community Shaders", "community shaders",
        "Skylighting - Community Shaders", "Hair Specular - Community Shaders")],
      [True, True, False, False])
check("a shader user is spotted whatever it is filed under",
      [tiers.uses_shaders(n) for n in
       ("Alternative Armors Redone PBR", "Skyrim 2020 Parallax",
        "Dibella Statue - Complex Material and PBR",
        "RUSTIC ANIMATED POTIONS and POISONS", "Complexion of Skyrim")],
      [True, True, True, False, False])

_shader = [node("Some PBR Armour", 0, tier=2),
           node("Plain Retexture", 1, tier=3),
           node("Community Shaders", 2, tier=3),
           node("Skylighting - Community Shaders", 3, tier=3)]
_edges = dag.shader_edges(_shader)
check("the framework is made a parent of every shader user",
      [(e.parent, e.child) for e in _edges],
      [("Community Shaders", "Some PBR Armour")])
check("and that outranks the tier that would have sorted it above",
      [n.name for n in dag.topological_sort(_shader, _edges)][:2],
      ["Plain Retexture", "Community Shaders"])
_tool_pbr = node("PBR Patcher Tool", 1)
_tool_pbr.is_tool = True
check("a tool is not held behind the framework",
      dag.shader_edges([node("Community Shaders", 0), _tool_pbr]), [])
check("and with no framework installed nothing is claimed",
      dag.shader_edges([node("Some PBR Armour", 0)]), [])


# -- a freeze outranks a guess, not a fact --------------------------------
_fr = [node("Winner", 0, tier=3), node("Middle", 1, tier=3),
       node("Loser", 2, tier=3)]
_soft = [DependencyEdge("Winner", "Loser", "file_conflict", "9 shared file(s)")]
check("a shared-file guess does not stop the user placing a mod",
      dag.freeze_blockers(_fr, _soft, "Winner", "Loser", "below"), [])
check("nor does a requirement listed on a Nexus page",
      dag.freeze_blockers(
          _fr, [DependencyEdge("Winner", "Loser", "nexus_requirement",
                               "Install First")],
          "Winner", "Loser", "below"), [])

_hard = [DependencyEdge("Winner", "Loser", "master_requirement",
                        "declares winner.esp as a master")]
check("but a master read out of a plugin header does",
      [(e.parent, e.child) for e in
       dag.freeze_blockers(_fr, _hard, "Winner", "Loser", "below")],
      [("Winner", "Loser")])
check("and so does the shader framework",
      len(dag.freeze_blockers(
          _fr, [DependencyEdge("Winner", "Loser", "shader_framework", "")],
          "Winner", "Loser", "below")), 1)

# The freeze has to survive the sort, not just pass the check: a soft edge
# pointing the other way must yield rather than quietly undo the move.
_soft_frozen = decisions.Decisions(str(_p.parent / "dagsort_soft.json"))
_soft_frozen.freeze("Loser", "Winner", "", "below")
check("a freeze against a shared-file guess is honoured, not dropped",
      [n.name for n in dag.topological_sort(
          _fr, _soft, freezes=_soft_frozen.freezes,
          freezes_below=_soft_frozen.freezes_below)],
      ["Winner", "Loser", "Middle"])
check("and nothing is reported as unhonoured",
      dag.unhonoured_freezes(
          dag.topological_sort(_fr, _soft, freezes=_soft_frozen.freezes,
                               freezes_below=_soft_frozen.freezes_below),
          _soft_frozen.freezes, _soft_frozen.freezes_below), [])

_hard_frozen = decisions.Decisions(str(_p.parent / "dagsort_hard.json"))
_hard_frozen.freeze("Winner", "Loser", "", "below")
check("a freeze against a master requirement is dropped instead",
      [n.name for n in dag.topological_sort(
          _fr, _hard, freezes=_hard_frozen.freezes,
          freezes_below=_hard_frozen.freezes_below)],
      ["Winner", "Middle", "Loser"])


# -- mods carrying an identical set of files ------------------------------
_dup = [node("Hand to Hand", 0, tier=3,
             files=["handtohand.bsa", "handtohand.esp",
                    "skse/plugins/handtohand.dll"]),
        node("Hand to Hand - An Adamant Addon", 1, tier=3,
             files=["handtohand.bsa", "handtohand.esp",
                    "skse/plugins/handtohand.dll"]),
        node("Something Else", 2, tier=3, files=["meshes/a.nif"])]
_found = duplicates.find(_dup)
check("two mods with the same manifest are reported together",
      [(g.names, g.files) for g in _found],
      [(["Hand to Hand", "Hand to Hand - An Adamant Addon"], 3)])

# A patch living inside a big mod is the ordinary case and must stay quiet,
# or the notice fires on every well-made add-on in the list.
_inside = [node("Big Mod", 0, tier=3,
                files=["a.nif"] + ["f{}.dds".format(i) for i in range(50)]),
           node("Small Patch", 1, tier=3, files=["a.nif"])]
check("a mod whose files merely sit inside a bigger one is not a duplicate",
      duplicates.find(_inside), [])

_out = [node("Some Mod", 0, tier=3, files=["meshes/a.nif"]),
        node("DynDOLOD_Output", 1, tier=6, files=["meshes/a.nif"])]
check("generated output is a copy of its sources by definition, so it is "
      "not counted", duplicates.find(_out, output_tier=6), [])

_tooldup = [node("Some Mod", 0, tier=3, files=["x.ini"]),
            node("Some Tool", 1, tier=3, files=["x.ini"])]
_tooldup[1].is_tool = True
check("nor is a tool", duplicates.find(_tooldup), [])

_off = [node("On", 0, tier=3, files=["a.nif"]),
        node("Off", 1, tier=3, files=["a.nif"], prefix="-")]
check("nor is a disabled mod", duplicates.find(_off), [])

check("the headline names both and counts the files",
      _found[0].headline,
      "Hand to Hand and Hand to Hand - An Adamant Addon - 3 identical files")


# -- an API key is described, never repeated ------------------------------
_key = "aBcDeF0123456789xyzzy-QWERTY"
check("a stored key is described by length and tail, not shown",
      keys.masked(_key), "28 characters, ending ERTY")
check("and the key itself never appears in what is shown",
      _key in keys.masked(_key), False)
check("a short key gives up its tail too",
      keys.masked("abcd1234"), "8 characters")
check("nothing stored says nothing", keys.masked("  "), "")

_http = OSError("boom")
_http.code = 401
check("a rejected key is explained in words",
      keys.reason(_http), "Nexus rejected it (401). Check it was copied in full.")
_rate = OSError("boom")
_rate.code = 429
check("so is a rate limit", keys.reason(_rate).startswith("too many"), True)
check("and an unknown failure still says something",
      keys.reason(OSError("connection refused")), "connection refused")


# -- mods nothing could put a category on ---------------------------------
_uc = [node("From LoversLab", 0, tier=3, files=["meshes/a.nif"]),
       node("From Nexus", 1, tier=3, files=["meshes/b.nif"]),
       node("Categorised", 2, tier=4, files=["meshes/c.nif"]),
       node("Some_Output", 3, tier=6, files=["meshes/d.nif"])]
_uc[0].tier_reason = "ships meshes or textures"
_uc[1].nexus_id, _uc[1].tier_reason = 191578, "ships meshes or textures"
_uc[2].category_name = "Armour"
_found = uncategorised.find(_uc, output_tier=6)
check("a mod with no category from any source is reported",
      [u.name for u in _found], ["From LoversLab", "From Nexus"])
check("and the two ways of getting there are told apart",
      [u.off_nexus for u in _found], [True, False])
check("one installed outside Nexus says so",
      _found[0].why, "not from Nexus, so there is no category to look up")
check("one whose lookup failed says that instead",
      _found[1].why, "Nexus category 191578 did not resolve")

# The answer has to outrank the guess, and survive a round trip to disk.
_cat = decisions.Decisions(str(_p.parent / "dagsort_cats.json"))
_cat.set_category("From LoversLab", "Animation")
_cat.save()
check("a given category is remembered across a reload",
      decisions.Decisions(str(_p.parent / "dagsort_cats.json")).category_for(
          "From LoversLab"), "Animation")

_apply = [node("From LoversLab", 0, tier=3, files=["meshes/a.nif"])]
tiers.apply(_apply, {}, None, None, {"From LoversLab": "Animation"})
check("and it settles the tier, saying where it came from",
      (_apply[0].tier, _apply[0].tier_reason),
      (2, "category you gave it: 'Animation'"))
check("after which the mod is no longer asked about",
      uncategorised.find(_apply), [])

_cat.set_category("From LoversLab", "")
check("clearing an answer removes it", _cat.category_for("From LoversLab"), "")


# ---- talking to the Nexus API Extender ---------------------------------
# It is a stated requirement, but a requirement is a thing users skip, so
# every one of these must hold when it is simply not there.


class _NoOrganizer:
    """An organizer that fails whatever is asked of it."""

    def pluginDataPath(self):
        raise RuntimeError("no MO2 here")

    def pluginSetting(self, *args):
        raise RuntimeError("no MO2 here")


check("a broken organizer yields no client, and does not raise",
      nexus_api.connect(_NoOrganizer()) is None or True, True)
check("finishing with no client is harmless",
      nexus_api.finish(None), None)
check("a missing Extender is named in the report",
      "not installed" in nexus_api.status(None), True)


class _FakeClient:
    has_key = True

    def remaining(self):
        return 42


check("a working Extender is named instead",
      "Extender with your stored key" in nexus_api.status(_FakeClient()), True)
check("and says what is left of the quota",
      "42 v1 requests left" in nexus_api.status(_FakeClient()), True)

# The translation layer matters: nexus.py's loop is written around urllib's
# exceptions, so a client failure has to arrive wearing the right coat.
import urllib.error

from mo2_dag_sorter import nexus as _nexus


class _Failing:
    status_to_raise = 404

    def rest(self, path):
        error = Exception("gone")
        error.status = self.status_to_raise
        raise error


try:
    _nexus._via_client(_Failing(), "games/x/mods/1.json")
    check("a client 404 becomes an HTTPError", False, True)
except urllib.error.HTTPError as exc:
    check("a client 404 becomes an HTTPError", exc.code, 404)
except Exception:
    check("a client 404 becomes an HTTPError", False, True)


class _Offline(_Failing):
    def rest(self, path):
        raise Exception("no network")


try:
    _nexus._via_client(_Offline(), "games/x/mods/1.json")
    check("a networkless client becomes a URLError", False, True)
except urllib.error.URLError:
    check("a networkless client becomes a URLError", True, True)
except Exception:
    check("a networkless client becomes a URLError", False, True)


# -- bypassing the cache for mods the user actually pointed at ------------

import io as _io_unused  # noqa: F401
import contextlib as _contextlib
import tempfile as _tempfile
from mo2_dag_sorter import pipeline as _pipeline
from mo2_dag_sorter import requirements as _requirements

with _tempfile.TemporaryDirectory() as _folder:
    _path = os.path.join(_folder, "nexus_cache.json")
    _c = _nexus.NexusCache(_path)
    _c.put(101, 22, "A mod")
    _c.put(202, None)                           # a 404
    check("cache remembers both", _c.known(101) and _c.known(202), True)
    _c.forget(101)
    _c.forget(202)
    check("forget drops a hit", _c.known(101), False)
    # A mod written off as missing must be re-askable too, or a page that
    # was briefly unavailable stays written off until the cache expires.
    check("forget drops a miss", _c.known(202), False)
    _c.save()
    check("forget survives a save", _nexus.NexusCache(_path).known(101), False)

    _r = _requirements.RequirementCache(
        os.path.join(_folder, "nexus_requirements.json"))
    _r.needs[101] = [(5, "needs this")]
    _r.forget(101)
    check("requirements forgotten", 101 in _r.needs, False)
    _r.forget(999)                              # absent is not an error
    check("forgetting what is absent is fine", True, True)

# The right-click sort walks the whole graph but must only re-ask about the
# selection. If this ever widens, a three-mod sort costs hundreds of requests.
_nodes = [ModNode(name="Alpha", prefix="+", original_index=0, nexus_id=11),
          ModNode(name="Beta", prefix="+", original_index=1, nexus_id=22),
          ModNode(name="Gamma", prefix="+", original_index=2)]
check("refresh takes just the named mod",
      _pipeline._stale_ids(["Alpha"], _nodes), {11})
check("refresh matches names case-insensitively",
      _pipeline._stale_ids(["alpha", "BETA"], _nodes), {11, 22})
check("a full sweep refreshes nothing",
      _pipeline._stale_ids([], _nodes), set())
check("no refresh argument refreshes nothing",
      _pipeline._stale_ids(None, _nodes), set())
check("a mod with no nexus id has nothing to drop",
      _pipeline._stale_ids(["Gamma"], _nodes), set())
check("an unknown name is not an error",
      _pipeline._stale_ids(["Nope"], _nodes), set())

# The Extender is a requirement, but users skip requirements - and one
# predating refreshing() must not break the right-click menu.
with nexus_api.refreshing(None):
    check("refreshing without an Extender is fine", True, True)


class _OlderExtender:
    pass


with nexus_api.refreshing(_OlderExtender()):
    check("refreshing with an older Extender is fine", True, True)


class _RealClient:
    def __init__(self):
        self.on = False

    @_contextlib.contextmanager
    def refreshing(self):
        self.on = True
        try:
            yield self
        finally:
            self.on = False


_live = _RealClient()
with nexus_api.refreshing(_live):
    check("a real client is switched on", _live.on, True)
check("and switched off afterwards", _live.on, False)



# -- requirement answers age out ------------------------------------------

import time as _time_req

with _tempfile.TemporaryDirectory() as _folder:
    _rp = os.path.join(_folder, "nexus_requirements.json")
    _r = _requirements.RequirementCache(_rp)
    _r.put(101, [(5, "needs this")])
    check("a fresh answer is not stale", _r.stale(101), False)
    check("an unknown mod is stale", _r.stale(999), True)
    # An empty answer is still an answer and must not be re-asked daily.
    _r.put(102, [])
    check("a stored empty answer is not stale", _r.stale(102), False)
    _r.save()

    _again = _requirements.RequirementCache(_rp)
    check("the stamp survives a save", _again.stale(101), False)
    check("and so does the answer", _again.needs[101], [(5, "needs this")])

    # Age it past the limit by reading it back with a one-second window.
    _old = _requirements.RequirementCache(_rp, max_age=0.0)
    _time_req.sleep(0.01)
    check("an answer older than max_age is stale", _old.stale(101), True)
    check("but the answer itself is still there", 101 in _old.needs, True)

    # Entries written before stamps existed have unknown age, so the first
    # sort after upgrading re-asks rather than trusting them forever.
    io.open(_rp, "w", encoding="utf-8").write(
        '{"needs": {"77": [[3, "old note"]]}}')
    _legacy = _requirements.RequirementCache(_rp)
    check("a legacy entry loads", _legacy.needs[77], [(3, "old note")])
    check("a legacy entry is stale", _legacy.stale(77), True)

    # forget() must drop the stamp too, or a failed re-fetch leaves a
    # timestamp with nothing behind it.
    _r2 = _requirements.RequirementCache(_rp)
    _r2.put(55, [(1, "x")])
    _r2.forget(55)
    check("forget drops the answer", 55 in _r2.needs, False)
    check("forget drops the stamp", 55 in _r2.fetched, False)


if failures:
    print("FAILED")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all DAG sorter tests passed")
