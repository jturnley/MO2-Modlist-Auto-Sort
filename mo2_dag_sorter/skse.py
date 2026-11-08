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

import dataclasses
import json
import os
import re
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
    names_address_library: bool = False   # the file name is in the DLL
    skse_required: int = 0         # oldest SKSE it accepts, packed; 0 = any

    def needs_newer_skse(self, skse_version) -> bool:
        """Would SKSE ``skse_version`` refuse this plugin as too old for it?

        SKSE compares the plugin's seVersionRequired against its own packed
        version and declines the plugin - "requires newer script extender"
        in its log - when the plugin asks for more. A value with no major
        version is not a version anyone wrote on purpose and is ignored.
        """
        if not self.skse_required or not skse_version:
            return False
        if (self.skse_required >> 24) < 1:
            return False
        return self.skse_required > pack_runtime(skse_version)

    @property
    def address_library(self) -> bool:
        """Does this plugin need Address Library to load?

        A 1.6-era plugin says so in its version block. An older one does
        not, but one that uses it carries the file name it opens - the
        same string that ends up in "failed to open address library
        file" when it is missing.
        """
        return bool(self.independence & ADDRESS_LIBRARY
                    or self.names_address_library)

    def loads_on(self, runtime) -> bool | None:
        """True, False, or None when there is no telling."""
        if not runtime or not (self.query or self.version_data):
            return None
        if is_vr(runtime):
            return None                 # see is_vr
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


# How a plugin built against Address Library names the file it opens:
# "Data/SKSE/Plugins/versionlib-{}.bin", "version-%d-%d-%d-%d.bin", or for
# VR "version-1-4-15-0.csv". Either slash; any case.
ADDRESS_LIBRARY_NAME = re.compile(
    rb"(?i)skse[/" + bytes((92, 92)) + rb"]plugins[/" + bytes((92, 92))
    + rb"]version(?:lib)?-|versionlib-")


def address_library_file(runtime) -> str | None:
    """The Address Library file this game version needs, lowercased and
    relative to the game's Data folder, or None if the version is unknown.

    Anniversary Edition reads versionlib-*.bin, SE 1.5 version-*.bin, and
    VR the CSV that "VR Address Library for SKSEVR" ships.
    """
    if not runtime:
        return None
    dashed = "-".join(str(v) for v in (tuple(runtime) + (0, 0, 0, 0))[:4])
    if is_vr(runtime):
        name = "version-{}.csv".format(dashed)
    elif tuple(runtime[:2]) >= (1, 6):
        name = "versionlib-{}.bin".format(dashed)
    else:
        name = "version-{}.bin".format(dashed)
    return DLL_DIR + name


# The SKSE plugin that makes ESL-flagged and .esl plugins work on VR.
VR_ESL_SUPPORT = DLL_DIR + "skyrimvresl.dll"


def is_vr(runtime) -> bool:
    """Is this Skyrim VR? Its executable is 1.4.15; no flat release was 1.4.

    On VR the export table says nothing useful. SKSE VR asks for
    SKSEPlugin_Query like SKSE on 1.5.97 does, so a flat-screen SE build and
    a VR build of the same plugin export the same thing, and both looked
    loadable. The newer build then won - and flat builds are usually
    numbered ahead of their VR ports, so the flat mod sorted over the VR
    one it was meant to lose to. Nothing in the file tells the two apart,
    so on VR the DLLs are not read for ordering at all, and those pairs are
    placed by the rules that placed them before DLLs were read.
    """
    return bool(runtime) and tuple(runtime[:2]) == (1, 4)


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
    info.names_address_library = ADDRESS_LIBRARY_NAME.search(data) is not None
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
    info.skse_required = struct.unpack_from("<I", data, tail + 8 + 64)[0]
    runtimes = []
    for i in range(16):
        value = struct.unpack_from("<I", data, tail + 8 + 4 * i)[0]
        if not value:
            break
        runtimes.append(value)
    info.runtimes = tuple(runtimes)


def unpack_version(packed: int) -> tuple:
    """The inverse of pack_runtime: (major, minor, build)."""
    return (packed >> 24, (packed >> 16) & 0xFF, (packed >> 4) & 0xFFF)


# -- SKSE itself ----------------------------------------------------------------

@dataclass
class SkseInstall:
    wanted: str            # the runtime DLL this game version needs
    loader: bool           # skse64_loader.exe / sksevr_loader.exe present
    found: bool            # ``wanted`` present
    version: tuple         # SKSE's own version, e.g. (2, 2, 8); () if unknown
    others: list           # runtime DLLs present for other game versions


SKSE_RUNTIME_DLL = re.compile(r"^(skse64|sksevr)_\d+_\d+_\d+\.dll$")


def skse_install(game_dir, runtime) -> SkseInstall | None:
    """What SKSE the game folder holds, judged against the game version.

    SKSE is one runtime DLL per game version - skse64_1_6_1170.dll - and a
    loader that refuses to start the game when the one matching the
    executable is missing. Its version resource reads 0.2.2.8 for SKSE
    2.2.8, so the leading zero is dropped.
    """
    if not game_dir or not runtime:
        return None
    prefix, loader = (("sksevr_", "sksevr_loader.exe") if is_vr(runtime)
                      else ("skse64_", "skse64_loader.exe"))
    wanted = "{}{}_{}_{}.dll".format(prefix, *tuple(runtime)[:3])
    try:
        files = {f.lower(): f for f in os.listdir(game_dir)}
    except OSError:
        return None
    others = sorted(f for f in files
                    if f != wanted and f.startswith(prefix)
                    and SKSE_RUNTIME_DLL.match(f))
    version = ()
    if wanted in files:
        try:
            with open(os.path.join(game_dir, files[wanted]), "rb") as fh:
                data = fh.read()
            directory, offset = _sections(data)
            raw = _file_version(data, directory, offset)
            version = tuple(raw[1:4]) if raw and raw[0] == 0 else tuple(raw[:3])
        except (OSError, ValueError, struct.error):
            version = ()
    return SkseInstall(wanted, loader in files, wanted in files, version,
                       others)


# -- reading each DLL once ---------------------------------------------------------
#
# A full read of a real list's SKSE plugins is 118 files and 338 MB, about
# two and a half seconds - too long to add to every game launch. Results are
# kept by path, size and modification time, and saved between sessions, so
# only a new or changed DLL is read again.

_cache: dict = {}
_dirty = False


def read_dll_cached(path: str) -> DllInfo | None:
    global _dirty
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = os.path.normcase(os.path.abspath(path))
    stamp = [st.st_size, st.st_mtime_ns]
    hit = _cache.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    info = read_dll(path)
    _cache[key] = (stamp, info)
    _dirty = True
    return info


def load_cache(path: str) -> None:
    """Merge a saved cache in. A missing or unreadable file is no cache."""
    try:
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return
    fields = {f.name for f in dataclasses.fields(DllInfo)}
    for key, (stamp, raw) in (saved.get("dlls") or {}).items():
        if key in _cache:
            continue
        try:
            info = None if raw is None else DllInfo(**{
                k: (tuple(v) if isinstance(v, list) else v)
                for k, v in raw.items() if k in fields})
        except TypeError:
            continue
        _cache[key] = (list(stamp), info)


def save_cache(path: str) -> None:
    global _dirty
    if not _dirty:
        return
    out = {key: (stamp, None if info is None else dataclasses.asdict(info))
           for key, (stamp, info) in _cache.items()}
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "dlls": out}, fh)
        os.replace(tmp, path)
        _dirty = False
    except OSError:
        pass                            # a cache that cannot be saved is fine


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
                info = read_dll_cached(os.path.join(mods_dir, node.name, rel))
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
    if is_vr(runtime):
        return None                     # see is_vr
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
