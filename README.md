# MO2 Modlist Auto Sort

An MO2 plugin that sorts the left pane into a working override order, and
explains every placement it makes.

**Tools → DAG Sorter**, plus a right-click menu on selected mods.

## How it works

```
parse -> metadata -> nexus -> conflicts -> build -> sort -> export
```

Mods are placed into tiers by what they are, then ordered inside a tier by a
dependency graph built from evidence. Kahn's algorithm with a heap keeps the
result stable: the sort key is `(tier, subtier, original index, name)`, so a
mod only moves when something actually requires it to.

### Where a mod's tier comes from

In descending authority:

1. **A category you gave it** — asked for once, in a dialog, for mods nothing
   else could identify. Kept in `user_rules.json`.
2. **Its Nexus category**, resolved through your instance's `categories.dat`.
3. **MO2's own category.**
4. **The file tree** — a guess from what the mod ships. Decent, but it reads
   what a mod *contains* rather than what it *is*. For a mod that packs its
   assets into a `.bsa`, the archive's name table is read too, so a quest mod
   shipped as `Quest.esp` + `Quest.bsa` is not mistaken for a bare patch. Only
   archives the game will actually open are read (see below); their contents
   never feed the conflict graph, because a loose file beats an archived one
   whatever the left pane says.

Tools (BodySlide, Nemesis, Pandora, FNIS, PG Patcher, xEdit…) are detected
and excluded from being sorting requirements entirely — their *output* goes
at the bottom of the list, but the "mod" that generates it is not something
other mods load after. One tool alone was dragging an entire PBR block ~400
slots down the list before this existed.

### Where the edges come from

Ordered by authority, strongest first:

| edge | meaning |
|---|---|
| `user_rule` | you said so — always wins |
| `master_requirement` | a plugin's masters. A fact |
| `asset_path` | where files land. A fact |
| `shader_framework` | Community Shaders before anything using shaders |
| `skse_runtime` | two mods ship the same SKSE DLL and only one copy loads on your game version. A fact, read from the DLL's exports |
| `skse_version` | two mods ship the same SKSE DLL and both load: the newer build wins |
| `nexus_requirement` | the mod page's Requirements list |
| `name_extension` | `X - Patch for Y` is a placement statement |
| `file_conflict` | overlapping files, weakest and the first to yield |

Freezing a stack yields to soft edges and refuses on hard ones, so a freeze
can override a file conflict but cannot break a master dependency.

A `file_conflict` edge still needs a direction. In order: tier, then surface
rank (PBR over parallax over plain), then **family** — `Ruins Clutter Improved
PBR` wins over `Ruins Clutter Improved` — then **shape**: a handful of files
landing inside a big mod is a patch to it, and a mod whose files mostly land
inside one many times its size is aimed at that corner of it, so the narrower
mod wins. Generated output is never reordered by shape. Failing all of those,
the order you already had is kept.

### What it tells you about

- **Unresolved conflicts** — pairs it could not settle, asked once and
  remembered.
- **Mods with no category** — where the placement is a guess, and why.
- **Identical mods** — separate mods shipping byte-identical file lists.
- **Shadowed mods** — entirely overridden by something above them.
- **Things that will not load at all**, whatever the order: a plugin whose
  master is missing (and why — disabled mod, unticked, or not installed), an
  archive no active plugin opens, an SKSE DLL built for another game version.
  **Reported only.** Nothing is disabled, moved or deleted on the strength of
  it. The game's own Data folder and `overwrite/` count as installed, so
  `Update.esm` and Creation Club content are never called missing.

## Layout

```
mo2_dag_sorter/       the plugin - copy this folder into MO2/plugins/
  pipeline.py         the whole sequence, no MO2 and no Qt in it
  dag.py              edges, cycles, Kahn's algorithm, freezes
  tiers.py            what a mod is, and how sure we are
  scan.py             the file tree, and what makes something a tool
  archives.py         .bsa name tables, for tiering only
  skse.py             what an SKSE DLL says about itself
  loadcheck.py        what will not load - reported, never acted on
  modlist.py          reading and writing modlist.txt
  nexus.py            v1 metadata + cache    requirements.py  v2 GraphQL
  decisions.py        user_rules.json        backups.py       undo
  *_ui.py             the dialogs
  nexus_api.py        every Nexus call, through the API Extender
  vault_key.py        the shared key, when only the key is wanted
tests/                run without MO2 or Qt
tools/dag_sort_cli.py sort and report from the command line
```

### MO2 priority direction, since it is easy to get backwards

`modlist.txt` stores **highest priority first**. The bottom of the file is
priority 0, which is the **top** of the left pane, which **loads first** and
therefore **loses** conflicts. Internal lists here are in priority order:
index 0 is the top of the pane.

Line prefixes: `+` enabled, `-` disabled, `*` unmanaged (DLC/CC, pinned by
MO2 at the end of the file).

Separators and unmanaged entries keep their exact line positions and the
sorted mods are poured into the slots left over, so a run never disturbs your
own headings.

## Requires: MO2 Nexus API Extender

Every Nexus call this plugin makes goes through the
[Nexus API Extender](https://github.com/jturnley/MO2-Nexus-API-Extender).
That means one stored key and **one response cache** shared with your
other plugins, rather than a second copy of each kept here: a mod this
sorter looked up is already paid for when something else asks about it, and
a second scan does not re-ask Nexus for what has not changed.

### Cached, except when you ask

A full sort is served from cache wherever it can be: it is walking hundreds
of mods and almost none of them changed since yesterday.

**The right-click sort re-asks Nexus about the mods you selected**, and only
those. If you picked a mod because its requirements look wrong, a cached
answer is the one thing that cannot help you. Whatever comes back replaces
the cached copy, so the next full sort has it too.

**Requirements older than a week are re-asked on a full sort.** Requirements
are the part of a mod page an author edits after release — a patch is added,
a dependency is dropped — and nothing in the API says when that last
happened, so the only question available is the whole question. It is one
batched request per twenty mods, over v2, which needs no key and bills
against a different quota from everything else here. Tiers are not aged:
a mod's category is set once and essentially never changes.

The first sort after upgrading to 0.9.1 re-checks everything, because
entries written by earlier versions carry no timestamp and could be any age.

### When a batch comes back refused

Twenty mods go out under one query, which is what makes a 700-mod sweep
affordable. It also means Nexus can refuse all twenty over one id it will
not answer for — `Nexus rejected the query: Mod not found` — and the
nineteen good ones go down with it.

A refused query and a dropped connection are told apart, because they want
opposite handling:

| What happened | What the sorter does |
| --- | --- |
| Nexus refused the query | Split the batch and retry each half, until the id causing it is alone |
| The connection failed | Wait and carry on with the next batch; stop after three in a row |
| One id still refused on its own | Stamp it and report it, so no later sort pays to find out again |
| An id comes back `null` | Stamp it, but keep any answer already stored |

Splitting is bounded — about a dozen requests to isolate one bad id out of
twenty, not one request per mod. Batches it never got to are left stale, so
the next sort retries exactly those and nothing else.

Both halves of that mattered. Before 0.9.4 the first failure stopped the
whole sweep, so one bad id partway through a 740-mod list cost the 380 mods
behind it, on every run. And the reason never reached the report, which said
only that nothing had been looked up — so the report now names the failure
and counts what was missed.

An id that answers when asked alone is not a broken mod and is not treated
as one. It is stamped like any other answer.

The rest of the graph still comes from cache during that run. Placing one mod
correctly means knowing what everything else is, so the whole list is walked
either way — re-asking about all of it would spend hundreds of requests to
answer a question about three.

It is listed as a requirement rather than an optional extra. But a
requirement is a thing people skip, so nothing here fails without it - the
plugin falls back to its own connection, and the report says which one it
used. What you lose is the shared cache and the protected key.

With the Extender installed, **Tools → Nexus API Key** is the Extender's
dialog and this plugin does not add a second one of its own. Its own key
dialog only appears when the Extender is absent, so there is never a choice
between two identically named entries where one writes to an encrypted file
and the other to plain text in `ModOrganizer.ini`.

**A key is not required.** Mod requirements come from v2 GraphQL, which needs
no credential, so they resolve for everyone. A key is what lets the tiering
look up a game's category table on v1 when MO2's own connection is
unavailable.

## If the right-click entries go missing

MO2 has no plugin API for the mod list's right-click menu — it is built in
C++ — so the sorter attaches itself to the widget instead. That attachment
can be lost. MO2 rebuilds the mod list when the set of installed plugins
changes, and removing an unrelated plugin has been seen to take these
entries with it.

A watchdog notices and puts them back within a few seconds. If it has not,
**Tools → Restore Right-Click Menu** does it immediately and tells you what
it found:

| What it says | What it means |
| --- | --- |
| The entries were missing. Attached. | Fixed. |
| They were already attached, and have been reattached anyway. | They were there; the flag can outlive the filter, so it hooked again regardless. |
| The sorter has not finished starting up yet. | Run it once MO2's window is up. |
| MO2's mod list could not be found in this window. | A newer MO2 renamed or restructured it. Please open an issue. |

Nothing is only reachable from that menu. Every action it offers is also on
the Tools menu, so a lost hook costs convenience and nothing else.

## Install

Copy `mo2_dag_sorter/` into `MO2/plugins/` and restart MO2.

## Tests

```bash
python tests/test_dag_sorter.py
```

No MO2, no Qt, no network. The Qt dialogs are not covered — PyQt6 is not
importable outside MO2's embedded Python, so they are syntax-checked only and
need a live run to verify.
