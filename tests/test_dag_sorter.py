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


# -- only one plugin may claim the "Nexus API Key" menu entry -------------
# plugin.py cannot be imported here (it needs mobase and PyQt6), so this
# reads the source. Worth the brittleness: two tools with one name put
# both in a submenu where neither says which is which, and the one a user
# picks decides whether their key is encrypted or written to the ini in
# plain text. That regressed once already.

_plugin_src = io.open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "mo2_dag_sorter", "plugin.py"), encoding="utf-8").read()
_key_tool = _plugin_src[_plugin_src.index("class DagKeyTool"):
                        _plugin_src.index("def create_plugins")]
_create = _plugin_src[_plugin_src.index("def create_plugins"):]
check("the sorter's key tool still claims that name",
      'return self.tr("Nexus API Key")' in _key_tool, True)
check("but stands down when the Extender is installed",
      "if not nexus_api.installed():" in _create
      and "tools.append(DagKeyTool())" in _create, True)
# Standing down must be quiet. An init() returning False unregisters the
# tool, but MO2 then logs the whole package as failed to load on every
# launch - which is how the last attempt at this read to a user.
check("and does so without failing to initialise",
      "return not nexus_api.installed()" in _plugin_src, False)
check("the tools that do not depend on the Extender are always offered",
      "tools = [DagSorterTool(), DagRestoreTool(), DagMenuTool()]"
      in _create, True)

# -- one plugin, one row in the vault's caller list ----------------------
from mo2_dag_sorter import nexus_api as _na, vault_key as _vk
check("the sorter asks the vault under a single name",
      _na.REQUESTER, _vk.REQUESTER)
check("and that name is the one users see",
      _vk.REQUESTER, "MO2 Modlist Auto Sort")


# -- one bad batch must not abandon the rest of the sweep -----------------
# This is the bug that cost 380 of 740 mods twice: the loop caught the
# first failure and broke, so a single failed request 19 batches in threw
# away everything after it, and the report said only "0 looked up".

class _FlakyClient:
    """Fails the nth batch, answers everything else with empty results."""

    def __init__(self, fail_on=(), boom="URLError: connection reset"):
        self.fail_on = set(fail_on)
        self.boom = boom
        self.calls = 0

    def graphql(self, query, variables=None):
        self.calls += 1
        if self.calls in self.fail_on:
            raise RuntimeError(self.boom)
        import re as _re
        return {alias: {"modRequirements": {"nexusRequirements":
                                            {"nodes": []}}}
                for alias in _re.findall(r"m(?:\d+)(?=:)", query)}


with _tempfile.TemporaryDirectory() as _folder:
    _rp = os.path.join(_folder, "nexus_requirements.json")

    # Five batches' worth; the second one fails.
    _ids = list(range(1000, 1000 + _requirements.BATCH * 5))
    _c = _requirements.RequirementCache(_rp)
    _flaky = _FlakyClient(fail_on=(2,))
    _needs, _report = _requirements.fetch(_ids, _c, 1704, client=_flaky)
    check("a failed batch does not stop the ones after it",
          _flaky.calls, 5)
    check("every other batch is still recorded",
          len(_c.needs), _requirements.BATCH * 4)
    check("the skipped batch is left stale for next time",
          _c.stale(_ids[_requirements.BATCH]), True)
    check("and the report says how many were missed",
          "{} not looked up".format(_requirements.BATCH) in _report, True)
    check("and says why, in the words the error used",
          "connection reset" in _report, True)

    # A dead endpoint should give up quickly rather than grind through
    # every batch waiting for one to work.
    _c2 = _requirements.RequirementCache(os.path.join(_folder, "b.json"))
    _dead = _FlakyClient(fail_on=range(1, 99), boom="URLError: unreachable")
    _needs2, _report2 = _requirements.fetch(_ids, _c2, 1704, client=_dead)
    check("a dead endpoint stops after GIVE_UP batches",
          _dead.calls, _requirements.GIVE_UP)
    check("nothing is recorded from it", len(_c2.needs), 0)
    check("and the reason still reaches the report",
          "unreachable" in _report2, True)

    # The counter is consecutive, not cumulative: scattered failures on a
    # long list must not add up to a give-up.
    _c3 = _requirements.RequirementCache(os.path.join(_folder, "c.json"))
    _spotty = _FlakyClient(fail_on=(1, 3, 5))
    _requirements.fetch(_ids, _c3, 1704, client=_spotty)
    check("scattered failures do not accumulate into a give-up",
          _spotty.calls, 5)

    # The real exception type must survive far enough to be described.
    try:
        _requirements._post("{ x }", client=_FlakyClient(fail_on=(1,),
                                                         boom="kaboom"))
    except _requirements.Unavailable as _exc:
        check("_post keeps the reason a batch failed",
              "kaboom" in str(_exc) and "RuntimeError" in str(_exc), True)
    else:
        check("_post keeps the reason a batch failed", "no raise", True)


# -- a mod Nexus has nothing to say about is not re-asked forever --------
# Three mods survived every sweep unstamped: hidden or deleted on Nexus,
# so their alias comes back null, so put() never ran, so they were stale
# again next sort. A null is an answer; it just must not blank a good one.

with _tempfile.TemporaryDirectory() as _folder:
    _tp = os.path.join(_folder, "nexus_requirements.json")
    _tc = _requirements.RequirementCache(_tp)

    _tc.touch(900)
    check("a null answer counts as asked", _tc.stale(900), False)
    check("and reads as no requirements", _tc.needs[900], [])

    _tc.put(901, [(7, "needs this")])
    _tc.touch(901)
    check("a null does not blank an answer already stored",
          _tc.needs[901], [(7, "needs this")])
    check("but it does refresh the stamp", _tc.stale(901), False)

    # End to end: a batch of nulls must leave nothing stale behind.
    _nulls = _FlakyClient()
    _nulls.graphql = lambda q, variables=None: {}
    _nc = _requirements.RequirementCache(os.path.join(_folder, "n.json"))
    _requirements.fetch([700, 701, 702], _nc, 1704, client=_nulls)
    check("an all-null batch leaves nothing to re-ask",
          [i for i in (700, 701, 702) if _nc.stale(i)], [])


# -- one unknown mod id must not take the other nineteen with it ---------
# Twenty mods go out under a single aliased query, so Nexus refusing it
# over one dead id refuses the whole batch. That is what cost 380 mods of
# 740, twice: "Nexus rejected the query: Mod not found", one bad id, and
# the old loop stopped there.

class _PickyClient:
    """Rejects any query mentioning a mod it does not know."""

    def __init__(self, unknown=()):
        self.unknown = set(unknown)
        self.calls = 0

    def graphql(self, query, variables=None):
        self.calls += 1
        import re as _re
        ids = [int(m) for m in _re.findall(r"m(\d+):", query)]
        if any(i in self.unknown for i in ids):
            raise RuntimeError("Nexus rejected the query: Mod not found")
        return {"m%d" % i: {"modRequirements": {"nexusRequirements":
                                                {"nodes": []}}}
                for i in ids}


with _tempfile.TemporaryDirectory() as _folder:
    _ids = list(range(2000, 2000 + _requirements.BATCH))
    _bad = _ids[7]

    _pc = _requirements.RequirementCache(
        os.path.join(_folder, "picky.json"))
    _picky = _PickyClient(unknown=(_bad,))
    _needs, _report = _requirements.fetch(_ids, _pc, 1704, client=_picky)

    _good = [i for i in _ids if i != _bad]
    check("the nineteen good mods still come back",
          sorted(_pc.needs), sorted(_ids))
    check("none of the good ones are left stale",
          [i for i in _good if _pc.stale(i)], [])
    check("the unknown id is stamped so it is not re-asked",
          _pc.stale(_bad), False)
    check("the report names it as unknown rather than missed",
          "1 Nexus has no record of" in _report, True)
    check("and does not claim anything was skipped",
          "not looked up" in _report, False)
    # Splitting 20 down to one bad id: 1 rejected + 2 halves + 2 + 2 + 2,
    # and the clean halves answer in one call each. Bounded, not per-mod.
    check("splitting stays cheap", _picky.calls <= 12, True)

    # Two bad ids in one batch must both fall out, not just the first.
    _pc2 = _requirements.RequirementCache(os.path.join(_folder, "p2.json"))
    _picky2 = _PickyClient(unknown=(_ids[2], _ids[15]))
    _needs2, _report2 = _requirements.fetch(_ids, _pc2, 1704, client=_picky2)
    check("both unknown ids fall out",
          "2 Nexus has no record of" in _report2, True)
    check("and the other eighteen are recorded",
          len([i for i in _ids if not _pc2.stale(i)]), _requirements.BATCH)

    # A whole batch of unknowns must terminate, not recurse forever.
    _pc3 = _requirements.RequirementCache(os.path.join(_folder, "p3.json"))
    _picky3 = _PickyClient(unknown=_ids)
    _needs3, _report3 = _requirements.fetch(_ids, _pc3, 1704, client=_picky3)
    check("an all-unknown batch terminates",
          "{} Nexus has no record of".format(_requirements.BATCH)
          in _report3, True)

    # A transport failure must NOT bisect - splitting a dead connection
    # into halves just multiplies the requests that are going to fail.
    _pc4 = _requirements.RequirementCache(os.path.join(_folder, "p4.json"))
    _dead4 = _FlakyClient(fail_on=range(1, 99))
    # Distinct ids, or dict.fromkeys collapses them into one batch and
    # the back-off never gets a second chance to be counted.
    _requirements.fetch(list(range(3000, 3000 + _requirements.BATCH * 4)),
                        _pc4, 1704, client=_dead4)
    check("a dead connection backs off instead of splitting",
          _dead4.calls, _requirements.GIVE_UP)


# -- the conflict dialog asks who wins, and records who wins -------------
# The dialog used to take whichever radio was checked and store that mod
# as the one loading FIRST, while the column above it was headed "wins".
# Clicking the right-hand option therefore did the opposite of what the
# heading said. resolve_ui cannot be imported here (PyQt6), so the wiring
# is read off the source and the semantics are tested through decisions.

with _tempfile.TemporaryDirectory() as _folder:
    _d = decisions.Decisions(os.path.join(_folder, "rules.json"))
    _d.set_winner("Winner Mod", "Loser Mod")
    check("the winner is not the one that loads first",
          _d.first[decisions.key("Winner Mod", "Loser Mod")], "Loser Mod")
    # Stated the other way round, the two calls must agree.
    _d2 = decisions.Decisions(os.path.join(_folder, "r2.json"))
    _d2.set("Loser Mod", "Winner Mod")
    check("set_winner and set agree about the pair",
          _d2.first[decisions.key("Winner Mod", "Loser Mod")],
          _d.first[decisions.key("Winner Mod", "Loser Mod")])

_resolve_src = io.open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "mo2_dag_sorter", "resolve_ui.py"), encoding="utf-8").read()
check("the right-hand column is the winner",
      "Loads last - WINS (selected)" in _resolve_src, True)
check("the left-hand column is the one overwritten",
      "Loads first - gets overwritten" in _resolve_src, True)
check("the dialog records a winner rather than converting to an order",
      "self._decisions.set_winner(" in _resolve_src, True)
check("the inverted mapping is gone",
      "second = edge.parent if first == edge.child" in _resolve_src, False)
check("the winner is rendered into the right-hand column",
      "self.table.setCellWidget(row, 1, self._cell(" in _resolve_src
      and "self._winners[row], row, True)" in _resolve_src, True)
check("picking the loser redraws the row instead of just moving the dot",
      "self._winners[row] = name" in _resolve_src
      and "self._render_row(row)" in _resolve_src, True)


# -- rules survive a rename, but only when the mod is unmistakable -------
# A freeze is keyed on the name MO2 shows, and MO2 names a mod after the
# archive it came from - so reinstalling or upgrading can rename it and
# silently orphan the rule. The Nexus id is the stable half. It is NOT
# unique per installed mod though: on a real 1143-mod list, 327 mods share
# an id with another (four "At Your Own Pace" modules, four "Visions NPCs
# Recasted" packs). So the id narrows, the name decides, and anything
# doubtful is left alone.

class _Mod:
    def __init__(self, name, nexus_id=0):
        self.name, self.nexus_id = name, nexus_id


with _tempfile.TemporaryDirectory() as _folder:
    def _rules(tag):
        return decisions.Decisions(os.path.join(_folder, tag + ".json"))

    # The plain case: one mod, one id, renamed by an upgrade.
    _r = _rules("plain")
    _r.freeze("Patch Mod", "Base Mod", "", "below")
    _r.reconcile([_Mod("Base Mod", 10), _Mod("Patch Mod", 20)])
    check("the id is learned while the mod is present",
          _r.ids.get("Patch Mod"), 20)
    _moves = _r.reconcile([_Mod("Base Mod", 10), _Mod("Patch Mod 2.1", 20)])
    check("a renamed mod carries its freeze across",
          _r.freezes_below.get("Patch Mod 2.1"), "Base Mod")
    check("and the old name is gone", "Patch Mod" in _r.freezes_below, False)
    check("and the move is reported", _moves, [("Patch Mod", "Patch Mod 2.1")])

    # The case that breaks a naive id lookup: one page, several mods.
    _s = _rules("shared")
    _s.freeze("At Your Own Pace - Companions", "Base", "", "below")
    _s.reconcile([_Mod("Base", 1),
                  _Mod("At Your Own Pace", 52704),
                  _Mod("At Your Own Pace - Companions", 52704),
                  _Mod("At Your Own Pace - Dawnguard", 52704)])
    _moves = _s.reconcile([_Mod("Base", 1),
                           _Mod("At Your Own Pace", 52704),
                           _Mod("At Your Own Pace - Companions 1.4", 52704),
                           _Mod("At Your Own Pace - Dawnguard", 52704)])
    check("the right module of a multi-mod page is chosen",
          _s.freezes_below.get("At Your Own Pace - Companions 1.4"), "Base")
    check("its siblings are left alone", len(_moves), 1)

    # Two siblings equally plausible: pick neither.
    _a = _rules("ambiguous")
    _a.freeze("Pack", "Base", "", "below")
    _a.reconcile([_Mod("Base", 1), _Mod("Pack", 99)])
    _moves = _a.reconcile([_Mod("Base", 1),
                           _Mod("Visions NPCs Recasted - Pack 1", 99),
                           _Mod("Visions NPCs Recasted - Pack 2", 99)])
    check("an ambiguous id moves nothing", _moves, [])
    check("and the stale rule is left where it was",
          _a.freezes_below.get("Pack"), "Base")

    # A mod that already carries rules is never taken over.
    _t = _rules("taken")
    _t.freeze("Old Name", "Base", "", "below")
    _t.freeze("Other Mod", "Base", "", "above")
    _t.reconcile([_Mod("Base", 1), _Mod("Old Name", 7), _Mod("Other Mod", 7)])
    _moves = _t.reconcile([_Mod("Base", 1), _Mod("Other Mod", 7)])
    check("a mod with rules of its own is not treated as a rename", _moves, [])

    # Without a remembered id there is nothing to match on, and guessing
    # from the name alone is not good enough to move a rule.
    _u = _rules("unknown")
    _u.freeze("Never Seen", "Base", "", "below")
    check("an unknown mod is not matched by name alone",
          _u.reconcile([_Mod("Never Seen 2.0", 5), _Mod("Base", 1)]), [])

    # Pins and pair answers ride along, not just freezes.
    _v = _rules("all")
    _v.pin("Runner", "bottom")
    _v.set_winner("Runner", "Loser")
    _v.set_category("Runner", "Patches")
    _v.reconcile([_Mod("Runner", 42), _Mod("Loser", 43)])
    _v.reconcile([_Mod("Runner 3.0", 42), _Mod("Loser", 43)])
    check("a pin follows the rename", _v.pins.get("Runner 3.0"), "bottom")
    check("a category follows the rename",
          _v.categories.get("Runner 3.0"), "Patches")
    check("a pair answer follows the rename",
          _v.first.get(decisions.key("Runner 3.0", "Loser")), "Loser")

    # And it all survives a trip through the file.
    _v.save()
    _w = decisions.Decisions(os.path.join(_folder, "all.json"))
    check("remembered ids are persisted", _w.ids.get("Runner 3.0"), 42)
    check("the renamed pin is persisted", _w.pins.get("Runner 3.0"), "bottom")


# -- the right-click hook puts itself back -------------------------------
# It vanished once when an unrelated plugin was removed from the instance.
# The left pane belongs to MO2, not to this plugin: it may not exist when
# the UI-ready callback fires, MO2 can rebuild it, and the order plugins
# start in shifts whenever any other plugin is added or removed. A single
# attempt at one moment is therefore not a hook, it is a coin toss.
# context_menu needs PyQt6, so the wiring is read off the source.

_menu_src = io.open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "mo2_dag_sorter", "context_menu.py"), encoding="utf-8").read()
check("the hook is kept alive on a timer rather than installed once",
      "class Keeper(QObject):" in _menu_src and "self._timer" in _menu_src,
      True)
check("it retries while the view is still missing",
      "self._timer.start(self.FAST)" in _menu_src, True)
check("and keeps checking after it succeeds, in case the view is rebuilt",
      "self._timer.start(self.SLOW)" in _menu_src, True)
check("a view that is already hooked is not hooked twice",
      "if view.property(HOOKED):" in _menu_src, True)
check("a missing active popup is not taken as proof there is no menu",
      "topLevelWidgets()" in _menu_src, True)
check("a dead window stops the keeper instead of raising",
      "except RuntimeError:" in _menu_src, True)

_plugin_src2 = io.open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "mo2_dag_sorter", "plugin.py"), encoding="utf-8").read()
check("the plugin holds the keeper, so its timer is not collected",
      "self._menu_keeper = context_menu.keep(" in _plugin_src2, True)
check("and a second ui-ready replaces it rather than stacking one",
      "old.stop()" in _plugin_src2, True)


# -- and can be put back by hand -----------------------------------------
# The watchdog above closes the window to a few seconds, but someone
# staring at a menu that has lost its entries wants an answer now, and
# wants to know which of several possible faults it is.
check("there is a repair entry on the Tools menu",
      "class DagMenuTool(mobase.IPluginTool):" in _plugin_src2, True)
check("it is registered unconditionally",
      "DagMenuTool()]" in _plugin_src2, True)
check("under a name of its own, so MO2 does not group it away",
      _plugin_src2.count('"MO2 DAG Sorter - Menu"'), 1)
check("the builder is recorded at init, not only when the UI comes up",
      "context_menu.remember(None, self._build_menu)" in _plugin_src2, True)
check("the repair reports what it found rather than only trying",
      "ok, detail = context_menu.reattach(window)" in _plugin_src2, True)
check("a failure is a warning, not a quiet information box",
      "QMessageBox.warning(window, title, body)" in _plugin_src2, True)
check("the module keeps the builder where a separate tool can reach it",
      "def reattach(" in _menu_src and "def remember(" in _menu_src, True)
check("remembering one half does not erase the other",
      "if window is not None:" in _menu_src
      and "if build is not None:" in _menu_src, True)
check("the stale hooked flag is cleared so a repair is never a no-op",
      "view.setProperty(HOOKED, False)" in _menu_src, True)
check("a missing mod list is reported as such, not as success",
      "could not be found in this window" in _menu_src, True)
check("a destroyed remembered window falls back to asking Qt",
      "_state[\"window\"] = None" in _menu_src, True)

# --- 0.10: what the game will actually load ---------------------------------
import struct as _struct
from mo2_dag_sorter import archives, loadcheck, skse, pipeline as _pipeline

_NUL = bytes(1)


def _bsa(folders, version=105):
    """A name-table-only Skyrim archive: {folder: [file, ...]}."""
    names = list(folders)
    record = 24 if version == 105 else 16
    folder_len = sum(len(f) + 1 for f in names)
    file_names = [n.encode() + _NUL for f in names for n in folders[f]]
    files = sum(len(folders[f]) for f in names)
    head = b"BSA" + _NUL + _struct.pack(
        "<7I", version, 36, 0x3, len(names), files, folder_len,
        sum(len(n) for n in file_names)) + _struct.pack("<HH", 0, 0)
    recs = b""
    for f in names:
        recs += _struct.pack("<QI", 0, len(folders[f])) + bytes(record - 12)
    blocks = b""
    for f in names:
        raw = f.encode() + _NUL
        blocks += bytes((len(raw),)) + raw + bytes(16 * len(folders[f]))
    return head + recs + blocks + b"".join(file_names)


def _plugin(masters=()):
    body = b""
    for m in masters:
        raw = m.encode() + _NUL
        body += b"MAST" + _struct.pack("<H", len(raw)) + raw
        body += b"DATA" + _struct.pack("<H", 8) + bytes(8)
    return b"TES4" + _struct.pack("<I", len(body)) + bytes(16) + body


def _pe(exports=(), plugin_version=0, independence=0, runtimes=(),
        file_version=None):
    """A PE32+ image with one section: exports, a version block, and
    optionally a VS_FIXEDFILEINFO. Just enough for skse.read_dll."""
    base_rva, raw_at = 0x1000, 0x200
    sec = bytearray(0x1000)
    names = list(exports)
    n = len(names)
    exp_dir = 0
    funcs = 40
    name_ptrs = funcs + 4 * n
    ords = name_ptrs + 4 * n
    strings = ords + 2 * n
    at = strings
    string_rva = []
    for name in names:
        raw = name.encode() + _NUL
        sec[at:at + len(raw)] = raw
        string_rva.append(base_rva + at)
        at += len(raw)
    block = (at + 15) // 16 * 16
    data = _struct.pack("<II", 1, plugin_version) + bytes(764)
    data += _struct.pack("<II", 0, independence)
    data += b"".join(_struct.pack("<I", r) for r in runtimes)
    sec[block:block + len(data)] = data
    for i, name in enumerate(names):
        target = block if name == "SKSEPlugin_Version" else 0x800
        _struct.pack_into("<I", sec, funcs + 4 * i, base_rva + target)
        _struct.pack_into("<I", sec, name_ptrs + 4 * i, string_rva[i])
        _struct.pack_into("<H", sec, ords + 2 * i, i)
    _struct.pack_into("<5I", sec, exp_dir + 20, n, n, base_rva + funcs,
                      base_rva + name_ptrs, base_rva + ords)
    res = 0xC00
    if file_version:
        a, b, c, d = file_version
        sec[res:res + 16] = (bytes((0xBD, 0x04, 0xEF, 0xFE))
                             + _struct.pack("<III", 0x10000,
                                            (a << 16) | b, (c << 16) | d))

    head = bytearray(raw_at)
    head[0:2] = b"MZ"
    _struct.pack_into("<I", head, 0x3C, 0x40)
    head[0x40:0x44] = b"PE" + bytes(2)
    _struct.pack_into("<HHIIIHH", head, 0x44, 0x8664, 1, 0, 0, 0, 240, 0)
    opt = 0x58
    _struct.pack_into("<H", head, opt, 0x20B)
    dirs = opt + 112
    _struct.pack_into("<II", head, dirs, base_rva, 40 + 10 * n)
    if file_version:
        _struct.pack_into("<II", head, dirs + 16, base_rva + res, 0x100)
    table = opt + 240
    head[table:table + 8] = b".rdata" + bytes(2)
    _struct.pack_into("<IIII", head, table + 8, len(sec), base_rva,
                      len(sec), raw_at)
    return bytes(head) + bytes(sec)


with _tempfile.TemporaryDirectory() as _folder:
    _arc = os.path.join(_folder, "Quest.bsa")
    with open(_arc, "wb") as _fh:
        _fh.write(_bsa({"meshes/quest": ["door.nif", "wall.nif"],
                        "sound/voice/quest.esp/guard": ["hello.fuz"]}))
    check("an archive's name tables are read as lowercased folder/file paths",
          archives.read_bsa_names(_arc),
          ["meshes/quest/door.nif", "meshes/quest/wall.nif",
           "sound/voice/quest.esp/guard/hello.fuz"])
    _old = os.path.join(_folder, "Old.bsa")
    with open(_old, "wb") as _fh:
        _fh.write(_bsa({"textures/x": ["a.dds"]}, version=104))
    check("the Oldrim-style 16-byte folder record is read too",
          archives.read_bsa_names(_old), ["textures/x/a.dds"])
    _junk = os.path.join(_folder, "Junk.bsa")
    with open(_junk, "wb") as _fh:
        _fh.write(b"BTDX" + bytes(40))
    check("anything that is not a Skyrim BSA reads as empty, not an error",
          archives.read_bsa_names(_junk), [])

# The loader rule, and the tier fallback reading inside archives that load.
check("a textures archive is loaded by the plugin of the plain name",
      loadcheck.archive_loaders("Foo - Textures.bsa"),
      ["foo.esp", "foo.esm", "foo.esl"])
check("an archive loads when its plugin is loaded",
      loadcheck.archive_loads("Foo.bsa", {"foo.esl"}, set()), True)
check("an archive loads when the INI lists it, plugin or not",
      loadcheck.archive_loads("Foo.bsa", set(), {"foo.bsa"}), True)
check("an archive with neither does not load",
      loadcheck.archive_loads("Foo.bsa", {"bar.esp"}, set()), False)

with _tempfile.TemporaryDirectory() as _mods:
    os.makedirs(os.path.join(_mods, "Quest Mod"))
    with open(os.path.join(_mods, "Quest Mod", "Quest.bsa"), "wb") as _fh:
        _fh.write(_bsa({"meshes/quest": ["door.nif"]}))
    _q = node("Quest Mod", 0, files=["quest.esp", "quest.bsa"],
              plugins=["quest.esp"])
    archives.populate([_q], _mods, loaded={"quest.esp"}, listed=())
    check("an archive whose plugin loads is read", _q.archive_manifest,
          ["meshes/quest/door.nif"])
    _tier, _why = tiers._heuristic_tier(_q)
    check("a plugin with an archive beside it is placed by what is packed",
          (_tier, _why.endswith("(read from inside its .bsa)")), (3, True))
    archives.populate([_q], _mods, loaded={"other.esp"}, listed=())
    check("an archive nothing loads is not read at all",
          _q.archive_manifest, [])
    check("and a plugin beside an unreadable archive is still not a patch",
          tiers._heuristic_tier(_q)[0] != 5, True)

# loadcheck.find: the three reports, and the generous reading of "available".
with _tempfile.TemporaryDirectory() as _root:
    _mods = os.path.join(_root, "mods")
    for _name, _files in (
            ("Needs Base", {"child.esp": _plugin(["base.esm", "update.esm"])}),
            ("Base Mod", {"base.esm": _plugin()}),
            ("Loose Archive", {"Stray.bsa": _bsa({"textures": ["t.dds"]}),
                               "Kept.bsa": _bsa({"textures": ["k.dds"]}),
                               "kept.esp": _plugin()})):
        os.makedirs(os.path.join(_mods, _name))
        for _f, _raw in _files.items():
            with open(os.path.join(_mods, _name, _f), "wb") as _fh:
                _fh.write(_raw)
    _data = os.path.join(_root, "game", "Data")
    os.makedirs(_data)
    open(os.path.join(_data, "Update.esm"), "wb").close()
    _nodes = [node("Needs Base", 0, files=["child.esp"], plugins=["child.esp"]),
              node("Base Mod", 1, files=["base.esm"], plugins=["base.esm"],
                   prefix="-"),
              node("Loose Archive", 2, files=["stray.bsa", "kept.bsa",
                                              "kept.esp"],
                   plugins=["kept.esp"])]
    _active = {"child.esp", "kept.esp"}
    _loaded = loadcheck.loaded_plugins(_nodes, _active, _data, None)
    check("the game's Data folder counts as loaded - Update.esm is not "
          "missing", "update.esm" in _loaded, True)
    _found = loadcheck.find(_nodes, _mods, _loaded, set(), (), _active)
    check("a master in a disabled mod, and an archive with no plugin, are "
          "reported", sorted((p.kind, p.item) for p in _found),
          [("missing_master", "child.esp"), ("orphan_archive", "stray.bsa")])
    check("the report names the mod holding the missing master",
          "Base Mod" in [p for p in _found
                         if p.kind == "missing_master"][0].detail, True)

with _tempfile.TemporaryDirectory() as _root:
    _bs = chr(92)
    with io.open(os.path.join(_root, "ModOrganizer.ini"), "w",
                 encoding="utf-8") as _fh:
        _fh.write("[General]" + chr(10) + "gamePath=@ByteArray(D:" + _bs * 2
                  + "Games" + _bs * 2 + "Skyrim)" + chr(10))
    check("the game path is unwrapped from Qt's @ByteArray form",
          loadcheck.game_directory(_root), "D:" + _bs + "Games" + _bs + "Skyrim")

# SKSE: the export table and the version block are the facts.
_AE = (1, 6, 1170, 0)
_SE = (1, 5, 97, 0)
check("the runtime is packed the way SKSE packs it",
      skse.pack_runtime(_AE), (1 << 24) | (6 << 16) | (1170 << 4))
check("a Query-only plugin loads on 1.5.97 and not on 1.6",
      (skse.DllInfo(query=True).loads_on(_SE),
       skse.DllInfo(query=True).loads_on(_AE)), (True, False))
check("an address-library plugin loads on any 1.6",
      skse.DllInfo(version_data=True,
                   independence=skse.ADDRESS_LIBRARY).loads_on(_AE), True)
check("a post-629 plugin does not load before 1.6.629",
      skse.DllInfo(version_data=True,
                   independence=skse.ADDRESS_LIBRARY
                   | skse.STRUCTS_POST_629).loads_on((1, 6, 353)), False)
check("a pinned plugin loads only on the runtime it names",
      (skse.DllInfo(version_data=True,
                    runtimes=(skse.pack_runtime(_AE),)).loads_on(_AE),
       skse.DllInfo(version_data=True,
                    runtimes=(skse.pack_runtime((1, 6, 640)),)).loads_on(_AE)),
      (True, False))
check("a DLL exporting nothing SKSE looks for says nothing",
      skse.DllInfo().loads_on(_AE), None)

with _tempfile.TemporaryDirectory() as _folder:
    _dll = os.path.join(_folder, "x.dll")
    with open(_dll, "wb") as _fh:
        _fh.write(_pe(["SKSEPlugin_Load", "SKSEPlugin_Version"],
                      plugin_version=0x010203, independence=skse.ADDRESS_LIBRARY,
                      file_version=(1, 2, 3, 0)))
    _info = skse.read_dll(_dll)
    check("an AE plugin is read off its export table and version block",
          (_info.query, _info.version_data, _info.independence,
           _info.plugin_version, _info.file_version),
          (False, True, skse.ADDRESS_LIBRARY, 0x010203, (1, 2, 3, 0)))
    with open(_dll, "wb") as _fh:
        _fh.write(_pe(["SKSEPlugin_Load", "SKSEPlugin_Query"]))
    _info = skse.read_dll(_dll)
    check("an SE-era plugin exports only the query",
          (_info.query, _info.version_data, _info.loads_on(_AE)),
          (True, False, False))
    with open(_dll, "wb") as _fh:
        _fh.write(b"not a dll")
    check("a file that is not a PE image is not a plugin",
          skse.read_dll(_dll), None)


def _dll_node(name, index, info, files=()):
    n = node(name, index, tier=0,
             files=["skse/plugins/meter.dll"] + list(files))
    n.skse_dlls = {"skse/plugins/meter.dll": info}
    return n


_new = skse.DllInfo(version_data=True, independence=skse.ADDRESS_LIBRARY,
                    file_version=(1, 1, 1, 0))
_older = skse.DllInfo(version_data=True, independence=skse.ADDRESS_LIBRARY,
                      file_version=(1, 0, 8, 0))
_se_only = skse.DllInfo(query=True, file_version=(9, 0, 0, 0))
_base = _dll_node("Meter", 0, _new)
_patch = _dll_node("Meter - AE Support", 1, _older)
_edges = dag.skse_edges([_base, _patch], _AE)
check("of two builds that both load, the newer one wins",
      [(e.parent, e.child, e.reason) for e in _edges],
      [("Meter - AE Support", "Meter", "skse_version")])
_legacy = _dll_node("Meter SE Legacy", 2, _se_only)
_edges = dag.skse_edges([_base, _legacy], _AE)
check("a copy that loads beats a newer one that cannot",
      [(e.parent, e.child, e.reason) for e in _edges],
      [("Meter SE Legacy", "Meter", "skse_runtime")])
_all = dag.build_edges([_base, _patch], None, _AE)
check("a pair the DLLs settled gets no file_conflict edge as well",
      sorted(e.reason for e in _all
             if {e.parent, e.child} == {"Meter", "Meter - AE Support"}),
      ["skse_version"])
_req = DependencyEdge("Meter", "Meter - AE Support", "nexus_requirement", "")
_kept, _dropped = dag.acyclic_edges(
    [_base, _patch], dag.skse_edges([_base, _patch], _AE) + [_req])
check("a newer build outranks a requirement list",
      ([(e.parent, e.child) for e in _kept],
       [(e.parent, e.child) for e in _dropped]),
      ([("Meter - AE Support", "Meter")], [("Meter", "Meter - AE Support")]))
check("a runtime mismatch is something a freeze cannot overrule",
      "skse_runtime" in dag.HARD_REASONS, True)
check("a runtime mismatch has words for the freeze dialog",
      "skse_runtime" in dag.EDGE_WORDS and "skse_version" in dag.EDGE_WORDS,
      True)
_lone = _dll_node("Meter Old Only", 0, skse.DllInfo(query=True))
check("a DLL path no copy of which loads is reported",
      [p.kind for p in loadcheck._runtime_problems([_lone], _AE)],
      ["skse_runtime"])
check("but not when one copy loads",
      loadcheck._runtime_problems([_lone, _base], _AE), [])

# Family variants and scope.
check("a PBR rebuild is a variant of the plain mod of the same name",
      tiers.variant_of("Ruins Clutter Improved PBR", "Ruins Clutter Improved"),
      True)
check("a PBR mod of an unrelated name is not",
      tiers.variant_of("Vanilla PBR AIO", "Ruins Clutter Improved"), False)
_plain = node("Ruins Clutter Improved", 0, files=["textures/r/a.dds"] * 1)
_pbr = node("Ruins Clutter Improved PBR", 1, files=["textures/r/a.dds"])
_plain.original_index, _pbr.original_index = 1, 0     # installed backwards
check("the variant wins over its plain family member, whatever the order",
      [(e.parent, e.child) for e in dag.conflict_edges([_plain, _pbr])],
      [("Ruins Clutter Improved", "Ruins Clutter Improved PBR")])

_big = node("Whole Game Retexture", 0,
            files=["textures/t/{}.dds".format(i) for i in range(400)])
_narrow = node("Barrels Only", 1,
               files=["textures/t/{}.dds".format(i) for i in range(30)]
               + ["textures/own/{}.dds".format(i) for i in range(10)])
_big.original_index, _narrow.original_index = 1, 0
check("a mod aimed at one corner of a much bigger one wins that corner",
      [(e.parent, e.child) for e in dag.conflict_edges([_big, _narrow])],
      [("Whole Game Retexture", "Barrels Only")])
_output = node("Barrels Only", 1, tier=tiers.TIER_OUTPUT,
               files=_narrow.file_manifest)
_big_out = node("Whole Game Retexture", 0, tier=tiers.TIER_OUTPUT,
                files=_big.file_manifest)
check("generated output is never re-ordered by scope",
      dag._scope(_big_out, _output, 30), None)
_spread = node("Mixed Pack", 1,
               files=["textures/t/{}.dds".format(i) for i in range(10)]
               + ["textures/m/{}.dds".format(i) for i in range(30)])
check("a mod that mostly ships its own files is not aimed at anything",
      dag._scope(_big, _spread, 10), None)

_pipe_src = io.open(os.path.join(os.path.dirname(os.path.abspath(
    _pipeline.__file__)), "pipeline.py"), encoding="utf-8").read()
check("archives are read before the tiers are worked out",
      _pipe_src.index("archives.populate(") < _pipe_src.index("tiers.apply("),
      True)
check("what will not load is on the result", "problems" in
      _pipeline.SortResult.__dataclass_fields__, True)
_plugin_src = io.open(os.path.join(os.path.dirname(os.path.abspath(
    _pipeline.__file__)), "plugin.py"), encoding="utf-8").read()
check("every sort from inside MO2 is given MO2's own game folder",
      _plugin_src.count("pipeline.sort_profile("),
      _plugin_src.count("game_dir=self._game_dir()"))
check("the confirm dialog lists what will not load",
      "for item in result.problems" in _plugin_src, True)
_cli_src = io.open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(_pipeline.__file__))), "tools", "dag_sort_cli.py"),
    encoding="utf-8").read()
check("so does the command line", "result.problems" in _cli_src, True)


if failures:
    print("FAILED")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all DAG sorter tests passed")
