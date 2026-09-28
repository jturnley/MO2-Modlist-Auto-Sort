"""The file names inside a mod's BSA archives, for tiering only.

A mod that packs everything into an archive looks, from its folder, like a
plugin and a .bsa - and the file-tree fallback in ``tiers`` reads "a plugin
and nothing else" as a patch. A quest mod with a voiced follower and new
dungeons is then tiered as a compatibility patch. Its archive says what it
really is, so the fallback is given the archive's contents to read.

**Not used for conflicts.** A loose file always beats an archived one
whatever the left pane says, and two archives are ordered by their plugins'
load order, not by mod priority. Feeding archived paths into the conflict
graph would invent edges the game does not honour. They go on their own
attribute and only the tier fallback looks at them.

Only the name tables are read - a few hundred kilobytes at most - never
the packed data, which can run to gigabytes.

    BSA (Skyrim, v103/104/105):
      "BSA" 0 | version | folder offset (36) | archive flags
      folder count | file count | folder names length | file names length
      file flags (u16) | pad (u16)
      folder records: hash u64, count u32, [pad u32], offset u32/u64
      per folder: [name as a length-prefixed string] + 16-byte file records
      file names: zero-terminated, in record order
"""

from __future__ import annotations

import os
import struct

BSA_MAGIC = b"BSA" + bytes(1)
HAS_FOLDER_NAMES = 0x1
HAS_FILE_NAMES = 0x2

# A name table larger than this is not a Skyrim archive.
MAX_TABLES = 32 << 20


def read_bsa_names(path: str) -> list[str]:
    """Lowercased ``folder/file`` paths in one archive, or [] if unreadable."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(36)
            if len(head) < 36 or head[:4] != BSA_MAGIC:
                return []
            (version, _offset, flags, folders, files,
             folder_len, file_len) = struct.unpack_from("<7I", head, 4)
            if version not in (103, 104, 105):
                return []
            if not (flags & HAS_FOLDER_NAMES and flags & HAS_FILE_NAMES):
                return []
            record = 24 if version == 105 else 16
            size = (folders * record + folders + folder_len
                    + files * 16 + file_len)
            if size > MAX_TABLES:
                return []
            data = fh.read(size)
    except OSError:
        return []

    try:
        counts = [struct.unpack_from("<I", data, i * record + 8)[0]
                  for i in range(folders)]
        pos = folders * record
        dirs: list[str] = []
        for count in counts:
            length = data[pos]
            dirs.append(data[pos + 1:pos + length].rstrip(bytes(1))
                        .decode("cp1252", "replace"))
            pos += 1 + length + 16 * count
        names = data[pos:pos + file_len].split(bytes(1))
    except (IndexError, struct.error):
        return []

    out: list[str] = []
    it = iter(names)
    for folder, count in zip(dirs, counts):
        base = folder.replace("\\", "/").lower().strip("/")
        for _ in range(count):
            try:
                name = next(it).decode("cp1252", "replace").lower()
            except StopIteration:
                return out
            out.append(base + "/" + name if base else name)
    return out


def populate(nodes, mods_dir: str, loaded=None, listed=()) -> None:
    """``archive_manifest`` for every enabled mod, from archives that load.

    An archive with no active plugin of its name, and not listed in the
    INI, is never opened by the game - so what is inside it says nothing
    about what the mod does in this load order, and it is not read. It
    shows up in ``loadcheck`` instead, which is where the user needs to
    hear about it. ``loaded`` of None means the plugin list could not be
    read, and every archive is taken as loading.
    """
    from . import loadcheck
    for node in nodes:
        node.archive_manifest = []
        if node.is_separator or node.is_unmanaged or not node.enabled:
            continue
        for rel in node.file_manifest:
            if "/" in rel or not rel.endswith(".bsa"):
                continue
            if loaded is not None and not loadcheck.archive_loads(
                    rel, loaded, set(listed or ())):
                continue
            node.archive_manifest.extend(
                read_bsa_names(os.path.join(mods_dir, node.name, rel)))
