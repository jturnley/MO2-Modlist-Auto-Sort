"""What will not load, said out loud. Never acted on.

Three ways part of a mod silently does nothing, all readable off disk:

* **A plugin whose master is missing.** The game refuses the load order.
* **An archive nothing loads.** ``Foo.bsa`` and ``Foo - Textures.bsa`` are
  opened only when a plugin called ``Foo.esp``/``.esm``/``.esl`` is active,
  or when the INI lists the archive. Otherwise every file in it is ignored,
  with no error anywhere.
* **An SKSE plugin built for another game version.** SKSE declines it and
  says so only in its own log.

This module reports those and does nothing else. It does not disable a
plugin, move a file into Optional ESPs, or edit a list. A report that is
wrong costs the user a minute; an automatic fix that is wrong can take a
quarter of a load order with it, which is not hypothetical.

That is also why "available" is read generously here. The game's Data
folder counts, because that is where the base game, Creation Club content
and anything installed outside MO2 live - a check that looks only in the
mods folder declares Update.esm missing. So does overwrite, which MO2 always
loads. The price of that generosity is a missed warning, never a false one.
"""

from __future__ import annotations

import configparser
import io
import os
from dataclasses import dataclass

PLUGIN_EXTS = (".esp", ".esm", ".esl")
TEXTURES_SUFFIX = " - textures"


@dataclass
class Problem:
    mod: str
    item: str          # the plugin, archive or DLL that will not load
    kind: str          # "missing_master" | "orphan_archive" | "skse_runtime"
                       # | "address_library" | "vr_esl"
    detail: str

    @property
    def headline(self) -> str:
        return "{} ({}): {}".format(self.item, self.mod, self.detail)


# -- where things are --------------------------------------------------------

def game_directory(mo2_root: str) -> str | None:
    """The game folder MO2 manages, from its own ModOrganizer.ini.

    For the command-line runner, which has no organizer to ask. The value
    is stored Qt-style, wrapped in @ByteArray(...) with every backslash
    doubled, so it is unwrapped rather than read raw.
    """
    path = os.path.join(mo2_root, "ModOrganizer.ini")
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, sep, value = line.partition("=")
                if sep and key.strip().lower() == "gamepath":
                    value = value.strip()
                    if value.startswith("@ByteArray(") and value.endswith(")"):
                        value = value[len("@ByteArray("):-1]
                    value = value.replace(BACKSLASH * 2, BACKSLASH)
                    return value or None
    except OSError:
        return None
    return None


BACKSLASH = chr(92)


def active_plugins(profile_dir: str) -> set | None:
    """Plugins ticked in the right pane, or None if the list is unreadable.

    MO2 leaves the base game and Creation Club plugins out of plugins.txt
    because the game loads them regardless; the Data folder stands in for
    them wherever this set is used.
    """
    path = os.path.join(profile_dir, "plugins.txt")
    try:
        with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
            lines = [l.strip() for l in fh]
    except OSError:
        return None
    return {l[1:].lower() for l in lines if l.startswith("*")}


def ini_archives(profile_dir: str) -> set:
    """Archives the INI tells the game to open whatever plugins are active."""
    found: set = set()
    for name in ("Skyrim.ini", "SkyrimCustom.ini"):
        path = os.path.join(profile_dir, name)
        if not os.path.isfile(path):
            continue
        cp = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            cp.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError, OSError):
            continue
        for section in cp.sections():
            for key in ("sresourcearchivelist", "sresourcearchivelist2"):
                if cp.has_option(section, key):
                    found.update(a.strip().lower()
                                 for a in cp.get(section, key).split(",")
                                 if a.strip())
    return found


def plugins_in(folder: str | None) -> set:
    if not folder or not os.path.isdir(folder):
        return set()
    try:
        return {f.lower() for f in os.listdir(folder)
                if f.lower().endswith(PLUGIN_EXTS)}
    except OSError:
        return set()


def loaded_plugins(nodes, active, data_dir, overwrite_dir) -> set:
    """Every plugin the game will load, as best the files can say.

    Without a readable plugins.txt, every plugin an enabled mod ships is
    taken as active - the generous reading, for the reason at the top.
    """
    shipped = set()
    for node in nodes:
        if node.enabled and not node.is_separator and not node.is_unmanaged:
            shipped.update(node.plugins)
    shipped |= plugins_in(overwrite_dir)
    on = shipped if active is None else (shipped & active)
    return on | plugins_in(data_dir)


def archive_loaders(archive: str) -> list[str]:
    """The plugin names that would make the game open ``archive``."""
    stem = archive.lower()[:-4]
    if stem.endswith(TEXTURES_SUFFIX):
        stem = stem[:-len(TEXTURES_SUFFIX)]
    return [stem + ext for ext in PLUGIN_EXTS]


def archive_loads(archive: str, loaded: set, listed: set) -> bool:
    low = archive.lower()
    return low in listed or any(p in loaded for p in archive_loaders(low))


# -- the report --------------------------------------------------------------

def installed(rel: str, live, data_dir=None, overwrite_dir=None,
              mods_dir=None) -> bool:
    """Is this file (lowercased, Data-relative) anywhere the game sees it?

    The mod folders are checked on disk, not only through file_manifest:
    the scan keeps only the file types sorting cares about, and Address
    Library's .bin and .csv are not among them. Trusting the manifest alone
    reported a correctly installed Address Library as missing.
    """
    parts = rel.split("/")
    for node in live:
        if rel in node.file_manifest:
            return True
        if mods_dir and os.path.isfile(os.path.join(mods_dir, node.name,
                                                    *parts)):
            return True
    for folder in (data_dir, overwrite_dir):
        if folder and os.path.isfile(os.path.join(folder, *rel.split("/"))):
            return True
    return False


def light_works(nodes, runtime, data_dir=None, overwrite_dir=None,
                mods_dir=None) -> bool:
    """Do ESL-flagged plugins count as light in this game?

    Everywhere but Skyrim VR, yes. On VR only with Skyrim VR ESL Support,
    an SKSE plugin; without it an .esl does not load at all and an ESL
    flag on an .esp is ignored.
    """
    from . import skse
    if not skse.is_vr(runtime):
        return True
    live = [n for n in nodes
            if n.enabled and not n.is_separator and not n.is_unmanaged]
    return installed(skse.VR_ESL_SUPPORT, live, data_dir, overwrite_dir,
                     mods_dir)


def _winners(live) -> dict:
    """{plugin: the mod whose copy the game reads} - the last in order."""
    out = {}
    for node in live:
        for plugin in node.plugins:
            out[plugin] = node
    return out


def find(nodes, mods_dir: str, loaded: set, listed: set,
         runtime=(), active=None, data_dir=None,
         overwrite_dir=None) -> list[Problem]:
    """Everything that will not load, in list order. Reads, never writes.

    Pass the nodes in the order being judged: where two mods ship the
    same plugin, only the copy that wins is the one the game reads, so it
    is the only one whose masters matter. Beeing Female NG ships its own
    FMR- Immersive Effects.esp; the FMR mod's copy needs Fertility
    Mode.esm, and reporting that while Beeing Female's copy wins would be
    a false alarm.
    """
    live = [n for n in nodes
            if n.enabled and not n.is_separator and not n.is_unmanaged]
    owner = {}
    for node in nodes:
        if node.is_separator or node.is_unmanaged:
            continue
        for plugin in node.plugins:
            owner.setdefault(plugin, node)
    winner = _winners(live)
    in_overwrite = plugins_in(overwrite_dir)

    out: list[Problem] = []
    for node in live:
        for plugin in node.plugins:
            if active is not None and plugin not in active:
                continue            # switched off: its masters cannot matter
            if winner.get(plugin) is not node or plugin in in_overwrite:
                continue            # overridden: this copy is never read
            needs = (getattr(node, "plugin_masters", None) or {}).get(plugin)
            if needs is None:
                needs = _masters_of(node, plugin, mods_dir)
            for master in needs:
                if master in loaded:
                    continue
                out.append(Problem(node.name, plugin, "missing_master",
                                   _why_missing(master, owner, mods_dir,
                                                nodes)))
        for rel in node.file_manifest:
            if "/" in rel or not rel.endswith(".bsa"):
                continue
            if not archive_loads(rel, loaded, listed):
                out.append(Problem(
                    node.name, rel, "orphan_archive",
                    "nothing loads this archive - no active plugin named "
                    "{}".format(" / ".join(archive_loaders(rel)))))
    out.extend(_runtime_problems(live, runtime))
    out.extend(_address_library_problems(live, runtime, data_dir,
                                         overwrite_dir, mods_dir))
    out.extend(_vr_esl_problems(live, runtime, active, data_dir,
                                overwrite_dir, mods_dir))
    return out


LISTED = 5


def _named(items) -> str:
    items = list(items)
    shown = ", ".join(items[:LISTED])
    if len(items) > LISTED:
        shown += " and {} more".format(len(items) - LISTED)
    return shown


def _address_library_problems(live, runtime, data_dir, overwrite_dir,
                              mods_dir=None) -> list[Problem]:
    """SKSE plugins that need Address Library for a version not installed.

    Every plugin built on it opens one file at startup, named after the
    game version. Without it each one fails with "failed to open address
    library file" - one dialog per plugin - and does nothing. One problem
    is reported for the file rather than one per plugin, because one
    install fixes them all.
    """
    from . import skse
    need = skse.address_library_file(runtime)
    if need is None or installed(need, live, data_dir, overwrite_dir,
                                 mods_dir):
        return []
    winning = {}
    for node in live:
        for rel, info in (getattr(node, "skse_dlls", None) or {}).items():
            winning[rel] = (node, info)
    users = sorted(os.path.basename(rel) for rel, (_n, info)
                   in winning.items() if info.address_library)
    if not users:
        return []
    version = ".".join(str(v) for v in runtime[:3])
    return [Problem(
        "not installed", os.path.basename(need), "address_library",
        "{} SKSE plugin(s) need Address Library for game version {} and it "
        "is not installed, so none of them will load ({}). Install "
        "'Address Library for SKSE Plugins' for this version.".format(
            len(users), version, _named(users)))]


def _vr_esl_problems(live, runtime, active, data_dir, overwrite_dir,
                     mods_dir=None) -> list[Problem]:
    """.esl plugins on Skyrim VR with nothing to load them."""
    if light_works(live, runtime, data_dir, overwrite_dir, mods_dir):
        return []
    esls = sorted(p for p in _winners(live)
                  if p.endswith(".esl") and (active is None or p in active))
    if not esls:
        return []
    return [Problem(
        "{} plugins".format(len(esls)), ".esl plugins", "vr_esl",
        "Skyrim VR loads no .esl plugin without 'Skyrim VR ESL Support', "
        "which is not installed ({}). ESL-flagged .esp plugins load, but "
        "each takes a full slot.".format(_named(esls)))]


def _masters_of(node, plugin, mods_dir):
    from . import masters as masters_mod
    return masters_mod.read_masters(os.path.join(mods_dir, node.name, plugin))


def _why_missing(master, owner, mods_dir, nodes) -> str:
    holder = owner.get(master)
    if holder is not None and not holder.enabled:
        return "needs {}, which is in {} - and that mod is switched " \
               "off".format(master, holder.name)
    if holder is not None:
        return "needs {}, which is installed in {} but unticked in the " \
               "plugin list".format(master, holder.name)
    for node in nodes:
        if node.is_separator or node.is_unmanaged:
            continue
        if os.path.isfile(os.path.join(mods_dir, node.name, "optional",
                                       master)):
            return "needs {}, which is in {}'s Optional ESPs".format(
                master, node.name)
    return "needs {}, which is not installed".format(master)


def _runtime_problems(live, runtime) -> list[Problem]:
    """A DLL path where no enabled mod's copy loads on this game version.

    Judged per path, not per copy: when one mod's build loads and
    another's does not, the sorter orders the loadable one on top and
    there is nothing to report.
    """
    if not runtime:
        return []
    copies: dict = {}
    for node in live:
        for rel, info in (getattr(node, "skse_dlls", None) or {}).items():
            copies.setdefault(rel, []).append((node, info))
    out = []
    version = ".".join(str(v) for v in runtime[:3])
    for rel in sorted(copies):
        verdicts = [info.loads_on(runtime) for _n, info in copies[rel]]
        if verdicts and all(v is False for v in verdicts):
            node = copies[rel][-1][0]
            out.append(Problem(
                node.name, os.path.basename(rel), "skse_runtime",
                "built for a different game version - SKSE will not load "
                "it on {}".format(version)))
    return out
