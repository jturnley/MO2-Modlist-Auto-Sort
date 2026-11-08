"""Will SKSE load what is installed? Asked before the game starts.

The sort's report says what will not load, but only when a sort is run.
A plugin that SKSE is going to refuse is worth knowing about at the moment
it costs something: when the game is about to start, and when the mod that
brought it has just been installed. MO2's own Script Extender Plugin
Checker reads SKSE's log - so it can only say what went wrong after the
game has run. Everything here is read before, from the files:

* **SKSE itself** - the runtime DLL for this game version
  (skse64_1_6_1170.dll), without which the loader will not start the game.
* **Each SKSE plugin against the game version** - the runtimes its export
  table and version block accept.
* **Each SKSE plugin against SKSE's version** - the oldest SKSE it says it
  accepts, which SKSE enforces.
* **Address Library**, for the plugins built on it.

Only the copy of a plugin that wins in the mod order is judged: that is the
only one SKSE sees. Nothing here disables, moves or changes anything.
"""

from __future__ import annotations

import os

from . import skse
from .loadcheck import LISTED, Problem

LAUNCHERS = frozenset(("skse64_loader.exe", "sksevr_loader.exe",
                       "skyrimse.exe", "skyrimvr.exe"))


def is_launch(path) -> bool:
    """Is this program the game, or SKSE's loader for it?"""
    return os.path.basename(str(path or "")).lower() in LAUNCHERS


def _child(folder, name):
    """``folder/name`` matched case-insensitively, or None."""
    try:
        for entry in os.listdir(folder):
            if entry.lower() == name:
                return os.path.join(folder, entry)
    except OSError:
        pass
    return None


def plugin_dlls(folder) -> dict:
    """{lowercased dll name: path} in ``folder``/SKSE/Plugins."""
    skse_dir = _child(folder, "skse") if folder else None
    plugins = _child(skse_dir, "plugins") if skse_dir else None
    if not plugins:
        return {}
    try:
        return {f.lower(): os.path.join(plugins, f)
                for f in os.listdir(plugins) if f.lower().endswith(".dll")}
    except OSError:
        return {}


def winning_dlls(mods, data_dir=None, overwrite_dir=None) -> dict:
    """{dll name: (where it comes from, path)} for the copies SKSE loads.

    ``mods`` is [(mod name, folder)] for the enabled mods, lowest priority
    first. The game folder is beneath them all and overwrite above.
    """
    out = {}
    layers = [("the game folder", data_dir)] + list(mods) + [
        ("overwrite", overwrite_dir)]
    for where, folder in layers:
        for name, path in plugin_dlls(folder).items():
            out[name] = (where, path)
    return out


def _has(rel, mods, data_dir, overwrite_dir) -> bool:
    parts = rel.split("/")
    for folder in [data_dir, overwrite_dir] + [f for _n, f in mods]:
        if folder and os.path.isfile(os.path.join(folder, *parts)):
            return True
    return False


def _dotted(version) -> str:
    return ".".join(str(v) for v in version)


def _judge(where, name, info, runtime, install) -> list[Problem]:
    """What is wrong with one plugin DLL, if anything."""
    if info is None or not (info.query or info.version_data):
        return []                  # not an SKSE plugin: SKSE ignores it
    if info.loads_on(runtime) is False:
        return [Problem(where, name, "skse_runtime",
                        "built for a different game version - SKSE will not "
                        "load it on {}".format(_dotted(runtime[:3])))]
    if install is not None and install.version \
            and info.needs_newer_skse(install.version):
        return [Problem(where, name, "skse_too_old",
                        "needs SKSE {} or newer, and {} is installed - SKSE "
                        "will not load it".format(
                            _dotted(skse.unpack_version(info.skse_required)),
                            _dotted(install.version)))]
    return []


def _skse_problems(runtime, install) -> list[Problem]:
    if install is None or install.found:
        return []
    detail = "SKSE for game version {} is not installed".format(
        _dotted(runtime[:3]))
    if install.others:
        detail += " (the game folder has {}, which is for another game " \
                  "version)".format(", ".join(install.others))
    detail += ", so the loader will not start the game and no SKSE plugin " \
              "will load"
    return [Problem("the game folder", install.wanted, "skse_missing", detail)]


def check(mods, game_dir, overwrite_dir=None) -> list[Problem]:
    """Everything SKSE would refuse in this setup.

    ``mods`` is [(mod name, folder)] for the enabled mods, lowest priority
    first. An unknown game version means nothing can be judged, and then
    nothing is claimed.
    """
    runtime = skse.game_runtime(game_dir)
    if not runtime:
        return []
    data_dir = os.path.join(game_dir, "Data")
    install = skse.skse_install(game_dir, runtime)
    out = _skse_problems(runtime, install)
    users = []
    for name, (where, path) in sorted(
            winning_dlls(mods, data_dir, overwrite_dir).items()):
        info = skse.read_dll_cached(path)
        out.extend(_judge(where, name, info, runtime, install))
        if info is not None and info.address_library \
                and info.loads_on(runtime) is not False:
            users.append(name)
    need = skse.address_library_file(runtime)
    if users and need and not _has(need, mods, data_dir, overwrite_dir):
        shown = ", ".join(users[:LISTED]) + (
            " and {} more".format(len(users) - LISTED)
            if len(users) > LISTED else "")
        out.append(Problem(
            "not installed", os.path.basename(need), "address_library",
            "{} SKSE plugin(s) need Address Library for game version {} and "
            "it is not installed, so none of them will load ({})".format(
                len(users), _dotted(runtime[:3]), shown)))
    return out


def check_mod(name, folder, game_dir) -> list[Problem]:
    """The SKSE plugins one mod ships, judged on their own.

    For the moment a mod is installed: whether something else overrides
    its DLL comes later, and what matters now is whether the copy just
    installed could load at all.
    """
    runtime = skse.game_runtime(game_dir)
    if not runtime:
        return []
    install = skse.skse_install(game_dir, runtime)
    out = []
    for dll, path in sorted(plugin_dlls(folder).items()):
        out.extend(_judge(name, dll, skse.read_dll_cached(path), runtime,
                          install))
    return out


def fingerprint(problem) -> str:
    """A stable name for one problem, for "do not warn me about this"."""
    return "{}|{}|{}".format(problem.kind, problem.item, problem.mod)
