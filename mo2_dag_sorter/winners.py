"""Which mod provides each file that matters, before and after a sort.

Every regression a user has reported against the sorter so far has had the
same shape: the new order quietly changed which mod's copy of something the
game reads - a flat-screen SKSE plugin over its VR port, a full-size plugin
over the ESL-flagged copy made to replace it, a copy of a plugin whose
masters are not installed over one whose masters are. Each was fixed with a
rule. This is the net under the rules: whatever the reason, if the winning
copy of a plugin, an archive, an SKSE plugin or a script changes, the
confirm dialog says so before anything is written.

Only these four kinds are listed. A texture changing hands is what sorting
is for; these are the files where the copy decides whether something works.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

PLUGIN_EXTS = (".esp", ".esm", ".esl")


def kind_of(path: str) -> str | None:
    """The kind of file this is, for these purposes, or None."""
    if "/" not in path:
        if path.endswith(PLUGIN_EXTS):
            return "plugins"
        if path.endswith(".bsa"):
            return "archives"
        return None
    if (path.startswith("skse/plugins/") and path.endswith(".dll")
            and path.count("/") == 2):
        return "SKSE plugins"
    if path.startswith("scripts/") and path.endswith(".pex"):
        return "scripts"
    return None


KINDS = ("plugins", "archives", "SKSE plugins", "scripts")


def _contested(nodes) -> dict:
    """{path: kind} for the files of these kinds that two or more enabled
    mods ship. A file only one mod ships cannot change hands."""
    holders: dict = defaultdict(int)
    kinds = {}
    for node in nodes:
        if node.is_separator or node.is_unmanaged or not node.enabled:
            continue
        for path in node.file_manifest:
            kind = kind_of(path)
            if kind:
                holders[path] += 1
                kinds[path] = kind
    return {p: kinds[p] for p, n in holders.items() if n > 1}


def winners(order, paths) -> dict:
    """{path: the mod whose copy wins} for ``paths`` in this order."""
    wanted = set(paths)
    out = {}
    for node in order:
        if node.is_separator or node.is_unmanaged or not node.enabled:
            continue
        for path in node.file_manifest:
            if path in wanted:
                out[path] = node.name
    return out


@dataclass
class WinnerDiff:
    changes: list = field(default_factory=list)   # (kind, path, now, was)

    def __bool__(self) -> bool:
        return bool(self.changes)

    def of(self, kind: str) -> list:
        return [c for c in self.changes if c[0] == kind]

    @property
    def headline(self) -> str:
        parts = ["{} {}".format(len(self.of(k)), k) for k in KINDS
                 if self.of(k)]
        if not parts:
            return "no plugin, archive, SKSE plugin or script changes hands"
        return "{} change which mod provides them".format(", ".join(parts))


def diff(before, after) -> WinnerDiff:
    contested = _contested(after)
    was = winners(before, contested)
    now = winners(after, contested)
    out = WinnerDiff()
    for path in sorted(contested, key=lambda p: (KINDS.index(contested[p]),
                                                 p)):
        if was.get(path) != now.get(path):
            out.changes.append((contested[path], path, now.get(path),
                                was.get(path)))
    return out
