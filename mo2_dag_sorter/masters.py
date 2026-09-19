"""Master declarations, for `master_requirement` edges.

The spec asks for requirement edges from the Nexus API, which reports a mod
page's stated requirements.  Every plugin also states its requirements in its
own TES4 header, and that version is better: it is exact, it names files
rather than mod pages, and it needs no API key.  So requirement edges come
from here, and the edge reason says `master_requirement` rather than
`nexus_requirement` so a report never claims an authority it does not have.

    TES4 | uint32 data size | flags | formid | revision | version | unknown
    ---- 24 byte record header, then subrecords ----
    type (4) | uint16 size | data
    MAST -> zero-terminated master filename, one subrecord per master
"""

from __future__ import annotations

import os
import struct

PLUGIN_EXTS = (".esp", ".esm", ".esl")


def read_masters(path: str) -> list[str]:
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
            if len(head) < 24 or head[:4] != b"TES4":
                return []
            size = struct.unpack_from("<I", head, 4)[0]
            if size > 1 << 20:            # not a plugin header; refuse
                return []
            body = fh.read(size)
    except OSError:
        return []

    out: list[str] = []
    pos = 0
    while pos + 6 <= len(body):
        kind = body[pos:pos + 4]
        length = struct.unpack_from("<H", body, pos + 4)[0]
        pos += 6
        if kind == b"MAST":
            name = body[pos:pos + length].split(b"\x00", 1)[0]
            try:
                out.append(name.decode("cp1252").lower())
            except UnicodeDecodeError:
                pass
        pos += length
    return out


def populate(nodes, mods_dir: str) -> None:
    """Fill in ``plugins`` and ``masters``.

    Root-level plugins only: a loose copy under ``optional/`` is not active,
    and treating it as active would invent dependencies the game never sees.
    """
    for node in nodes:
        if node.is_separator or node.is_unmanaged or not node.enabled:
            continue
        names = [f for f in node.file_manifest
                 if "/" not in f and f.endswith(PLUGIN_EXTS)]
        if not names:
            continue
        node.plugins = names
        seen: list[str] = []
        for name in names:
            for master in read_masters(os.path.join(mods_dir, node.name, name)):
                if master not in seen:
                    seen.append(master)
        own = set(names)
        node.masters = [m for m in seen if m not in own]


def owners(nodes) -> dict[str, str]:
    """{plugin filename: the mod that provides it}, last one winning."""
    out: dict[str, str] = {}
    for node in nodes:
        for name in node.plugins:
            out[name] = node.name
    return out
