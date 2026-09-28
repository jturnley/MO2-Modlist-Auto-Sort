"""What an SKSE plugin DLL says about itself, read out of the file.

Two mods shipping the same ``skse/plugins/x.dll`` is not a texture clash.
Whichever copy loses is simply not there, and whichever wins has to be one
the game can load: a DLL built for the wrong runtime is refused by SKSE and
takes its whole mod with it. The usual file-conflict guess - tier, shape,
install order - has nothing to say about that. The DLL does:

* **Which runtimes it loads on.** An Anniversary-era plugin exports a data
  block named ``SKSEPlugin_Version`` stating the runtimes it supports; an
  older one exports only the function ``SKSEPlugin_Query``, which is all
  SKSE on 1.5.97 looks for and which SKSE on 1.6 no longer accepts alone.
  A build exporting both is meant for either.
* **Which build it is.** The version resource every Windows DLL carries,
  and the plugin version inside that data block.

The runtime is the game's own executable, read the same way. Nothing here
guesses from a mod's name: "AE Support" in a name is a claim, the export
table is the fact.

    PE: "MZ" ... e_lfanew @0x3C -> "PE" 0 0 | COFF (20) | optional header
        data directory 0 = exports, 2 = resources
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

DLL_DIR = "skse/plugins/"

# SKSEPluginVersionData.versionIndependence
ADDRESS_LIBRARY = 1 << 0
SIGNATURES = 1 << 1
STRUCTS_POST_629 = 1 << 2

# Past this, a DLL is not a plugin anyone ships, and reading it would be
# reading something else.
MAX_READ = 64 << 20

PE_MAGIC = b"PE" + bytes(2)
FIXED_INFO = bytes((0xBD, 0x04, 0xEF, 0xFE))


@dataclass
class DllInfo:
    query: bool = False            # exports SKSEPlugin_Query (1.5-era)
    version_data: bool = False     # exports SKSEPlugin_Version (1.6-era)
    independence: int = 0
    runtimes: tuple = ()           # exact runtimes, when not independent
    plugin_version: int = 0
    file_version: tuple = ()

    def loads_on(self, runtime) -> bool | None:
        """True, False, or None when there is no telling."""
        if not runtime or not (self.query or self.version_data):
            return None
        if tuple(runtime[:2]) < (1, 6):
            return self.query
        if not self.version_data:
            return False
        if self.independence & (ADDRESS_LIBRARY | SIGNATURES):
            if (self.independence & STRUCTS_POST_629
                    and tuple(runtime[:3]) < (1, 6, 629)):
                return False
            return True
        if self.runtimes:
            return pack_runtime(runtime) in self.runtimes
        return None

    @property
    def build(self) -> tuple:
        """Something to compare two builds of one plugin by."""
        return self.file_version or ((self.plugin_version,)
                                     if self.plugin_version else ())


def pack_runtime(version) -> int:
    """SKSE's REL::Version packing: major.minor.build, sub ignored."""
    major, minor, build = (tuple(version) + (0, 0, 0))[:3]
    return (major << 24) | (minor << 16) | ((build & 0xFFF) << 4)


def _sections(data: bytes):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != PE_MAGIC:
        raise ValueError("not a PE file")
    count = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dirs = opt + (112 if magic == 0x20B else 96)
    table = opt + opt_size
    sections = []
    for i in range(count):
        base = table + 40 * i
        vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((vaddr, max(vsize, rsize), rptr))

    def directory(index: int):
        return struct.unpack_from("<II", data, dirs + 8 * index)

    def offset(rva: int) -> int:
        for vaddr, size, rptr in sections:
            if vaddr <= rva < vaddr + size:
                return rva - vaddr + rptr
        raise ValueError("address outside every section")

    return directory, offset


def _cstring(data: bytes, at: int, limit: int = 256) -> str:
    end = data.find(bytes(1), at, at + limit)
    return data[at:end if end >= 0 else at + limit].decode("latin-1")


def _file_version(data: bytes, directory, offset) -> tuple:
    rva, size = directory(2)
    if not rva:
        return ()
    start = offset(rva)
    # VS_FIXEDFILEINFO opens with this signature wherever the resource
    # tree put it; walking the tree to find it would be longer and no
    # more certain.
    at = data.find(FIXED_INFO, start, start + size)
    if at < 0:
        return ()
    ms, ls = struct.unpack_from("<II", data, at + 8)
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def read_dll(path: str) -> DllInfo | None:
    """The facts, or None for anything that is not a readable plugin."""
    try:
        if os.path.getsize(path) > MAX_READ:
            return None
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if data[:2] != b"MZ":
        return None
    info = DllInfo()
    try:
        directory, offset = _sections(data)
        info.file_version = _file_version(data, directory, offset)
        rva, _size = directory(0)
        if not rva:
            return info
        exp = offset(rva)
        _n_funcs, n_names, funcs, names, ords = struct.unpack_from(
            "<IIIII", data, exp + 20)
        for i in range(n_names):
            name = _cstring(data, offset(struct.unpack_from(
                "<I", data, offset(names) + 4 * i)[0]))
            if name == "SKSEPlugin_Query":
                info.query = True
            elif name == "SKSEPlugin_Version":
                info.version_data = True
                ordinal = struct.unpack_from("<H", data, offset(ords) + 2 * i)[0]
                target = struct.unpack_from(
                    "<I", data, offset(funcs) + 4 * ordinal)[0]
                _version_block(data, offset(target), info)
    except (ValueError, struct.error, IndexError):
        pass                        # what was read before the fault stands
    return info


def _version_block(data: bytes, at: int, info: DllInfo) -> None:
    """SKSEPluginVersionData: two words, three strings, then the runtimes.

        u32 dataVersion | u32 pluginVersion | char name[256]
        char author[256] | char supportEmail[252]
        u32 versionIndependenceEx | u32 versionIndependence
        u32 compatibleVersions[16] (zero-terminated) | u32 seVersionRequired
    """
    info.plugin_version = struct.unpack_from("<I", data, at + 4)[0]
    tail = at + 8 + 256 + 256 + 252
    info.independence = struct.unpack_from("<I", data, tail + 4)[0]
    runtimes = []
    for i in range(16):
        value = struct.unpack_from("<I", data, tail + 8 + 4 * i)[0]
        if not value:
            break
        runtimes.append(value)
    info.runtimes = tuple(runtimes)


def game_runtime(game_dir: str | None) -> tuple:
    """The game executable's version, e.g. (1, 6, 1170, 0), or ()."""
    if not game_dir:
        return ()
    for exe in ("SkyrimSE.exe", "SkyrimVR.exe"):
        path = os.path.join(game_dir, exe)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            directory, offset = _sections(data)
            return _file_version(data, directory, offset)
        except (OSError, ValueError, struct.error):
            return ()
    return ()


def populate(nodes, mods_dir: str) -> None:
    """Read every SKSE plugin DLL an enabled mod ships, keyed by path."""
    for node in nodes:
        node.skse_dlls = {}
        if node.is_separator or node.is_unmanaged or not node.enabled:
            continue
        for rel in node.file_manifest:
            if (rel.startswith(DLL_DIR) and rel.endswith(".dll")
                    and rel.count("/") == 2):
                info = read_dll(os.path.join(mods_dir, node.name, rel))
                if info is not None:
                    node.skse_dlls[rel] = info


def verdict(a, b, runtime):
    """(loads first, loads after, reason, detail) for two mods sharing a
    DLL, or None when the DLLs do not settle it.

    One copy loading on this runtime and the other not is a fact, and
    comes first. Two copies that both load are then compared by build,
    newer winning: a later build of the same plugin carries the fixes the
    earlier one lacks, and nothing is gained by shipping the older.
    """
    shared = sorted(set(a.skse_dlls) & set(b.skse_dlls))
    for rel in shared:
        la = a.skse_dlls[rel].loads_on(runtime)
        lb = b.skse_dlls[rel].loads_on(runtime)
        if la is True and lb is False:
            return b, a, "skse_runtime", _runtime_detail(rel, a, b, runtime)
        if lb is True and la is False:
            return a, b, "skse_runtime", _runtime_detail(rel, b, a, runtime)
    for rel in shared:
        ba, bb = a.skse_dlls[rel].build, b.skse_dlls[rel].build
        if ba and bb and ba != bb:
            old, new = (a, b) if ba < bb else (b, a)
            return old, new, "skse_version", (
                "{}: {}'s copy is build {}, {}'s is {}".format(
                    os.path.basename(rel), new.name, _dotted(max(ba, bb)),
                    old.name, _dotted(min(ba, bb))))
    return None


def _dotted(version) -> str:
    return ".".join(str(v) for v in version)


def _runtime_detail(rel, good, bad, runtime) -> str:
    return "{}: {}'s copy loads on game version {}, {}'s does not".format(
        os.path.basename(rel), good.name, _dotted(runtime[:3]), bad.name)
