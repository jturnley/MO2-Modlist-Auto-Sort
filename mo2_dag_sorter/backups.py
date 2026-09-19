"""Timestamped copies of modlist.txt, and the way back.

The sorter rewrites a file the user has spent months building by hand, so
every write keeps the previous version, dated, with a note of what caused
it.  One rolling ``.bak`` is not enough: a bad sort followed by an install
would overwrite the only good copy before the user noticed anything wrong.

Restoring is itself a change, so it takes a backup too.  That makes undo
symmetrical - the user can restore, decide they preferred the sorted list
after all, and get it back.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass

DIRNAME = "modlist_backups"
STAMP = "%Y-%m-%d_%H%M%S"
NAME = re.compile(r"^modlist_(\d{4}-\d{2}-\d{2}_\d{6})(?:_([^.]*))?\.txt$")
KEEP = 60


@dataclass
class Backup:
    path: str
    when: float                  # epoch seconds, from the filename
    label: str                   # what caused it: "sort", "install", ...
    mods: int                    # entries in the file, for telling them apart

    @property
    def stamp(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.when))

    @property
    def headline(self) -> str:
        return "{}   {:>5} mods   {}".format(
            self.stamp, self.mods, self.label or "manual")


def folder(list_path: str) -> str:
    return os.path.join(os.path.dirname(list_path), DIRNAME)


def _count(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for line in fh if line[:1] in "+-*")
    except OSError:
        return 0


def make(list_path: str, label: str = "") -> str | None:
    """Copy the current list aside. Returns the backup's path."""
    if not os.path.isfile(list_path):
        return None
    into = folder(list_path)
    try:
        os.makedirs(into, exist_ok=True)
    except OSError:
        return None
    safe = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")
    base = "modlist_{}{}.txt".format(time.strftime(STAMP),
                                     "_" + safe if safe else "")
    target = os.path.join(into, base)
    # Two writes in the same second - an install that triggers a sort - must
    # not silently overwrite each other.
    n = 1
    while os.path.exists(target):
        target = os.path.join(into, base[:-4] + "-{}.txt".format(n))
        n += 1
    try:
        shutil.copy2(list_path, target)
    except OSError:
        return None
    prune(list_path)
    return target


LEGACY = ("modlist.txt.bak", "modlist.pre-dag-sort.txt")


def adopt(list_path: str) -> int:
    """Take in copies left beside modlist.txt by older tools.

    A rolling ``.bak`` next to the list is invisible to the restore dialog
    and is the first thing overwritten, yet it is often the only copy of the
    list as the user built it by hand.  Bringing them in costs one directory
    listing and makes them selectable like anything else; the timestamp comes
    off the file, so re-running this adopts nothing twice.
    """
    where = os.path.dirname(list_path)
    into = folder(list_path)
    taken = 0
    try:
        names = os.listdir(where)
    except OSError:
        return 0
    for name in names:
        low = name.lower()
        if not (low in LEGACY or low.startswith("modlist.txt.bak")):
            continue
        src = os.path.join(where, name)
        if not os.path.isfile(src):
            continue
        stamp = time.strftime(STAMP, time.localtime(os.path.getmtime(src)))
        target = os.path.join(into, "modlist_{}_recovered.txt".format(stamp))
        if os.path.exists(target):
            continue
        try:
            os.makedirs(into, exist_ok=True)
            shutil.copy2(src, target)
            taken += 1
        except OSError:
            continue
    return taken


def available(list_path: str) -> list[Backup]:
    """Every backup for this profile, newest first."""
    adopt(list_path)
    into = folder(list_path)
    out: list[Backup] = []
    try:
        names = os.listdir(into)
    except OSError:
        return out
    for name in names:
        match = NAME.match(name)
        if not match:
            continue
        try:
            when = time.mktime(time.strptime(match.group(1), STAMP))
        except ValueError:
            continue
        path = os.path.join(into, name)
        out.append(Backup(path, when, match.group(2) or "", _count(path)))
    out.sort(key=lambda b: b.when, reverse=True)
    return out


def latest(list_path: str) -> Backup | None:
    found = available(list_path)
    return found[0] if found else None


def restore(list_path: str, backup_path: str) -> str | None:
    """Put a backup back. The list being replaced is itself backed up first."""
    if not os.path.isfile(backup_path):
        return None
    saved = make(list_path, "before-restore")
    tmp = list_path + ".tmp"
    shutil.copy2(backup_path, tmp)
    os.replace(tmp, list_path)
    return saved


def prune(list_path: str, keep: int = KEEP) -> None:
    """Keep the newest ``keep``; a profile should not grow backups for ever."""
    found = available(list_path)
    for old in found[keep:]:
        try:
            os.remove(old.path)
        except OSError:
            pass
