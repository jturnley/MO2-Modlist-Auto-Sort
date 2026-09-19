"""Run the DAG sorter outside MO2. Previews by default; --apply writes."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mo2_dag_sorter import backups, dag, pipeline


def _backups(list_path, args) -> int:
    """The restore half of the plugin, for when MO2 will not start."""
    found = backups.available(list_path)
    if args.backups or not found:
        for backup in found:
            print(" ", backup.headline)
        if not found:
            print("no backups yet")
        return 0
    if args.undo:
        chosen = found[0]
    else:
        matches = [b for b in found if b.stamp.startswith(args.restore)]
        if not matches:
            print("no backup matching", args.restore)
            return 1
        chosen = matches[0]
    saved = backups.restore(list_path, chosen.path)
    print("restored", chosen.headline)
    print("previous list saved as", saved)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="MO2 instance folder")
    ap.add_argument("--profile", default="Default")
    ap.add_argument("--api-key", default=os.environ.get("NEXUS_API_KEY", ""))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--backups", action="store_true",
                    help="list the dated backups of this profile's mod list")
    ap.add_argument("--undo", action="store_true",
                    help="restore the most recent backup")
    ap.add_argument("--restore", metavar="STAMP",
                    help="restore the backup whose timestamp starts with this")
    args = ap.parse_args()

    try:
        list_path = os.path.join(args.root, "profiles", args.profile,
                                 "modlist.txt")
        if args.backups or args.undo or args.restore:
            return _backups(list_path, args)

        result = pipeline.sort_profile(args.root, args.profile,
                                       api_key=args.api_key or None)
    except dag.CycleError as exc:
        print("CYCLE among {} mods:".format(len(exc.nodes)))
        for edge in exc.edges[:40]:
            print("  {} -> {}  ({}, {})".format(
                edge.parent, edge.child, edge.reason, edge.detail))
        return 2

    print(result.report)
    for name, old, new in result.moved[:args.top]:
        print("  {:>4} -> {:<4} {}".format(old, new, name))
    if len(result.moved) > args.top:
        print("  ... and {} more".format(len(result.moved) - args.top))
    if result.dropped:
        print("")
        print("dropped to break cycles ({}):".format(len(result.dropped)))
        for edge in result.dropped[:10]:
            print("  {} -> {}  ({}, {})".format(
                edge.parent, edge.child, edge.reason, edge.detail))
    if result.unresolved:
        print("")
        print("conflicts settled by rule, awaiting your answer ({}):".format(
            len(result.unresolved)))
        for edge in result.unresolved:
            print("  {} loads first (loses) | {} loads second (wins)   [{}]"
                  .format(edge.child, edge.parent, edge.reason))
    if result.uncategorised:
        off = sum(1 for u in result.uncategorised if u.off_nexus)
        print()
        print("no category from any source - placed by the file tree "
              "({}, {} of them not from Nexus):".format(
                  len(result.uncategorised), off))
        for item in result.uncategorised[:20]:
            print("  " + item.headline)
    if result.duplicates:
        print()
        print("these mods replicate exactly the same files - worth checking "
              "they are meant to ({}):".format(len(result.duplicates)))
        for group in result.duplicates:
            print("  " + group.headline)
    if result.shadowed:
        print("")
        print("fully overridden - these contribute nothing ({}):".format(
            len(result.shadowed)))
        for item in result.shadowed[:20]:
            print("  " + item.headline)
    if args.apply:
        print("backup:", pipeline.apply_result(args.root, args.profile, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
