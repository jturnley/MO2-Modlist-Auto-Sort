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
   what a mod *contains* rather than what it *is*.

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
| `nexus_requirement` | the mod page's Requirements list |
| `name_extension` | `X - Patch for Y` is a placement statement |
| `file_conflict` | overlapping files, weakest and the first to yield |

Freezing a stack yields to soft edges and refuses on hard ones, so a freeze
can override a file conflict but cannot break a master dependency.

### What it tells you about

- **Unresolved conflicts** — pairs it could not settle, asked once and
  remembered.
- **Mods with no category** — where the placement is a guess, and why.
- **Identical mods** — separate mods shipping byte-identical file lists.
- **Shadowed mods** — entirely overridden by something above them.

## Layout

```
mo2_dag_sorter/       the plugin - copy this folder into MO2/plugins/
  pipeline.py         the whole sequence, no MO2 and no Qt in it
  dag.py              edges, cycles, Kahn's algorithm, freezes
  tiers.py            what a mod is, and how sure we are
  scan.py             the file tree, and what makes something a tool
  modlist.py          reading and writing modlist.txt
  nexus.py            v1 metadata + cache    requirements.py  v2 GraphQL
  decisions.py        user_rules.json        backups.py       undo
  *_ui.py             the dialogs
  vault_key.py        optional: borrow the shared key from the API Extender
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

## Optional: shared Nexus key

If the **MO2 Nexus API Extender** is installed, the sorter borrows the key it
holds rather than keeping one of its own in `ModOrganizer.ini`. Entirely
optional — not installed is the normal case, and nothing here fails without
it. Its own `nexus_api_key` setting still works and still wins if set.

## Install

Copy `mo2_dag_sorter/` into `MO2/plugins/` and restart MO2.

## Tests

```bash
python tests/test_dag_sorter.py
```

No MO2, no Qt, no network. The Qt dialogs are not covered — PyQt6 is not
importable outside MO2's embedded Python, so they are syntax-checked only and
need a live run to verify.
