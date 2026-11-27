"""Component B, second half: what each mod actually contains.

Two things come off disk here.  The mod's ``meta.ini`` gives the Nexus mod id,
which Component C turns into a tier; the mod's files give the asset manifest,
which Component D turns into override edges.

Only files that can collide in the virtual file system are kept.  A mod's
readme and its BodySlide project files never override anything, and carrying
them would make the manifest several times larger for no edges.
"""

from __future__ import annotations

import configparser
import os

ASSET_EXTS = (".dds", ".nif", ".hkx", ".dll", ".esl", ".esp", ".esm",
              ".bsa", ".pex", ".psc", ".seq", ".ini", ".json", ".swf",
              ".wav", ".xwm", ".fuz", ".tri", ".txt")

# Extensions whose presence says something about what a mod *is*, used by the
# offline tiering fallback.
PLUGIN_EXTS = (".esp", ".esm", ".esl")

# Files that share a name across mods without overriding anything. The
# extension whitelist above lets .txt through because MCM and SPID
# configuration lives in .txt files, which drags every mod's readme in with
# it: five mods on the test list ship a root-level "readme.txt", and without
# this they claim to be in conflict with each other.
IGNORED_NAMES = frozenset((
    "meta.ini", "readme.txt", "read me.txt", "changelog.txt", "changes.txt",
    "license.txt", "licence.txt", "credits.txt", "desktop.ini",
    "thumbs.db", ".ds_store",
))


def read_meta(mod_dir: str) -> tuple[int | None, str, str, list]:
    """(Nexus mod id, file date, version, MO2 category ids) from meta.ini.

    The date is ``nexusLastModified``: when the file that was installed was
    last updated on Nexus, not when the user installed it.  It is read for
    the resolve dialog rather than for sorting.  When two mods override each
    other and nothing on disk says which should win, which one was built
    later is real evidence - and it is the evidence a person actually
    reaches for - but reading it is a judgement, not a rule, so the plugin
    shows it and lets the user decide.
    """
    path = os.path.join(mod_dir, "meta.ini")
    try:
        info = os.stat(path)
    except OSError:
        return None, "", "", []
    # Parsing 1,200 meta.ini files with configparser is most of a second,
    # and between two sorts nearly none of them change. Keyed on the file's
    # own modification time and size, so an edited one is always re-read.
    stamp = (info.st_mtime_ns, info.st_size)
    hit = _META.get(path)
    if hit is not None and hit[0] == stamp:
        mod_id, date, version, cats = hit[1]
        return mod_id, date, version, list(cats)
    found = _parse_meta(path)
    _META[path] = (stamp, found)
    mod_id, date, version, cats = found
    return mod_id, date, version, list(cats)


_META: dict = {}


def _parse_meta(path: str) -> tuple[int | None, str, str, list]:
    if not os.path.isfile(path):
        return None, "", "", []
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    for encoding in ("utf-8", "cp1252"):
        try:
            cp.read(path, encoding=encoding)
            break
        except (UnicodeDecodeError, configparser.Error, OSError):
            continue
    else:
        return None, "", "", []
    if not cp.has_section("General"):
        return None, "", "", []
    general = cp["General"]
    raw = general.get("modid", "").strip().strip('"')
    try:
        value = int(raw)
    except ValueError:
        value = 0
    stamp = general.get("nexusLastModified", "").strip().strip('"')[:10]
    version = general.get("version", "").strip().strip('"')
    # category="5,12," - MO2's own numbering, and the first is the one the
    # left pane shows.
    cats = [int(c) for c in general.get("category", "").strip().strip('"').split(",")
            if c.strip().isdigit()]
    return (value if value > 0 else None), stamp, version, cats


def scan_mod_dir(mod_dir: str) -> list[str]:
    """Relative asset paths, lowercased with forward slashes.

    Lowercased because the game's file system is case-insensitive: two mods
    shipping ``Textures/`` and ``textures/`` are overriding each other, and a
    case-sensitive comparison would miss every one of those collisions.
    """
    out: list[str] = []
    _walk(mod_dir, "", out)
    return out


def _walk(folder: str, prefix: str, out: list) -> None:
    """os.walk's answer, in os.walk's order, at about a third of the cost.

    os.walk asks Windows whether every directory is a link - one extra
    call each, 40,000 of them on a large list. ``os.scandir`` already
    knows, from the listing it has just read, so this asks nobody. The
    rules are os.walk's: a link to a directory is listed but not entered,
    a broken link counts as a file, an unreadable folder is skipped, and
    a folder's own files come before its subfolders'.
    """
    try:
        listing = os.scandir(folder)
    except OSError:
        return
    subdirs = []
    with listing:
        for entry in listing:
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                subdirs.append(entry)
                continue
            low = entry.name.lower()
            if low.endswith(ASSET_EXTS) and low not in IGNORED_NAMES:
                out.append(prefix + low)
    for entry in subdirs:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_symlink():
                continue
        except OSError:
            continue
        _walk(entry.path, prefix + entry.name.lower() + "/", out)


class SessionCache:
    """Each mod's file list, kept for as long as MO2 is running.

    Walking every mod folder is most of what a sort costs - several seconds
    on a list of a thousand mods - and installing one mod changes one
    folder. So the lists are kept between sorts and only re-read when
    something says the folder changed:

    * the folder's own modification time, or its meta.ini's, moved - which
      is what a reinstall, a merge or a replace does;
    * :meth:`forget` was called for it - the plugin does that when MO2
      reports a mod installed or removed;
    * :meth:`forget` was called with no path - the plugin does that after
      any program MO2 ran (BodySlide, Nemesis, DynDOLOD write into mod
      folders), and a full sort from the Tools menu always reads fresh.

    What it can miss is a file added deep inside a mod folder by hand in
    Explorer while MO2 is open. The next full sort, program run or restart
    picks that up.
    """

    def __init__(self) -> None:
        self._files: dict = {}

    @staticmethod
    def _stamp(path: str):
        try:
            folder = os.stat(path).st_mtime_ns
        except OSError:
            return None
        try:
            meta = os.stat(os.path.join(path, "meta.ini")).st_mtime_ns
        except OSError:
            meta = 0
        return folder, meta

    def get(self, name: str, path: str):
        held = self._files.get(os.path.normcase(path))
        if held is None:
            return None
        if held[0] != self._stamp(path):
            self._files.pop(os.path.normcase(path), None)
            return None
        return held[1]

    def put(self, name: str, path: str, files) -> None:
        stamp = self._stamp(path)
        if stamp is not None:
            self._files[os.path.normcase(path)] = (stamp, list(files))

    def forget(self, path: str | None = None) -> None:
        if path is None:
            self._files.clear()
            _META.clear()
        else:
            self._files.pop(os.path.normcase(path), None)

    def __len__(self) -> int:
        return len(self._files)


# The one the plugin uses. A module-level instance because MO2 loads the
# plugin once per session, which is exactly the lifetime wanted.
SESSION = SessionCache()


def populate(nodes, mods_dir: str, cache=None, progress=None,
             fresh: bool = False) -> None:
    """Fill in the meta.ini fields and ``file_manifest`` on every node.

    With a ``cache``, unchanged folders are not walked again. ``fresh``
    walks every folder regardless and refills the cache, which is what a
    full sort does: it is the one the user runs to get everything right.

    The folders that do need walking are walked a few at a time. Listing a
    directory is a wait on Windows, not work for Python, and several waits
    overlap; the lists come back in the same order either way.
    """
    total = len(nodes)
    todo = []
    for i, node in enumerate(nodes):
        if node.is_separator or node.is_unmanaged or not node.enabled:
            continue
        path = os.path.join(mods_dir, node.name)
        if not os.path.isdir(path):
            continue
        (node.nexus_id, node.file_date, node.version,
         node.mo2_categories) = read_meta(path)
        files = (cache.get(node.name, path)
                 if cache is not None and not fresh else None)
        if files is None:
            todo.append((i, node, path))
        else:
            node.file_manifest = files
    if not todo:
        return
    if progress is not None or len(todo) < 8:
        walked = map(scan_mod_dir, (path for _i, _n, path in todo))
        pool = None
    else:
        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=WORKERS)
        walked = pool.map(scan_mod_dir, [path for _i, _n, path in todo])
    try:
        for (i, node, path), files in zip(todo, walked):
            if cache is not None:
                cache.put(node.name, path, files)
            node.file_manifest = files
            if progress is not None and not progress(i, total, node.name):
                return
    finally:
        if pool is not None:
            pool.shutdown(wait=True)


WORKERS = 8


# The top-level names the game loads from Data. Anything a mod ships outside
# these, and outside a root-level plugin or archive, the game never reads.
#
# CalienteTools, SkyProc Patchers and Tools are deliberately absent. They sit
# inside Data and look like content, but they hold a tool's own project files
# - BodySlide's shapes, a patcher's configuration - and the game never opens
# any of it. What BodySlide builds from them lands in a separate output mod,
# which does get loaded and is ordered normally.
GAME_DIRS = frozenset((
    "meshes", "textures", "scripts", "sound", "music", "interface", "seq",
    "grass", "lodsettings", "dialogueviews", "strings", "skse", "f4se",
    "netscriptframework", "shadersfx", "video", "facegen", "materials",
    "source", "dyndolod", "bashtags", "mcm", "shaders",
))
ROOT_EXTS = (".esp", ".esm", ".esl", ".bsa")


def ships_game_assets(files) -> bool:
    """True if anything this mod ships is loaded by the game."""
    for path in files:
        head, _, rest = path.partition("/")
        if rest:
            if head in GAME_DIRS:
                return True
        elif path.endswith(ROOT_EXTS):
            return True
    return False


# Mods the user runs rather than loads. An external patcher is installed
# into the mods folder because that is where its own configuration lives,
# not because the game reads any of it, so where it sits in the left pane
# means nothing - and a tool's Nexus page being listed as a requirement is
# a statement about building the load order, not about load order itself.
#
# Both halves of the first test are needed and each is wrong alone: the
# category catches every SKSE framework, which mods genuinely do have to
# load after, and the file test catches SPID and Base Object Swapper mods,
# which ship nothing but a root-level _DISTR.ini and are ordinary content.
TOOL_CATEGORY = "utilities"

# The second test, for the tools that test cannot reach. A behaviour engine
# ships a stub plugin so that mods checking for FNIS are satisfied, which
# makes it look like content; it is still a program the user runs between
# sessions. These are product names rather than words, because the words
# belong to real mods: "Offset Movement Animation - Nemesis - Modders
# Resource" is an animation mod, "DynDOLOD Resources SE" ships the meshes
# every generated LOD is built from, and "Dyn FNIS AA functions" is an SKSE
# plugin. None of them name an engine.
TOOL_NAMES = (
    "bodyslide and outfit studio", "outfit studio",
    "pandora behaviour engine", "pandora behavior engine",
    "nemesis unlimited behavior engine", "nemesis behavior engine",
    "fnis behavior", "fores new idles",
    "dyndolod standalone", "xlodgen", "cathedral assets optimizer",
    "wrye bash", "sseedit", "xedit", "zedit", "creation kit",
)

# What a tool leaves behind is a mod like any other: it ships meshes,
# textures and plugins the game loads, and it has to be ordered.
OUTPUT_MARKERS = ("_output", "output_", " output", "bashed patch",
                  "smashed patch")


def looks_like_tool(category_name: str, files, name: str = "") -> bool:
    low = (name or "").strip().lower()
    if any(marker in low for marker in OUTPUT_MARKERS):
        return False
    if any(tool in low for tool in TOOL_NAMES):
        return True
    return ((category_name or "").strip().lower() == TOOL_CATEGORY
            and not ships_game_assets(files))
