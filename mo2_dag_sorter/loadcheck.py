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

def find(nodes, mods_dir: str, loaded: set, listed: set,
         runtime=(), active=None) -> list[Problem]:
    """Everything that will not load, in list order. Reads, never writes."""
    live = [n for n in nodes
            if n.enabled and not n.is_separator and not n.is_unmanaged]
    owner = {}
    for node in nodes:
        if node.is_separator or node.is_unmanaged:
            continue
        for plugin in node.plugins:
            owner.setdefault(plugin, node)

    out: list[Problem] = []
    for node in live:
        for plugin in node.plugins:
            if active is not None and plugin not in active:
                continue            # switched off: its masters cannot matter
            for master in _masters_of(node, plugin, mods_dir):
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
    return out


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
