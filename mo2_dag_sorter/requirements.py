"""Component C, second half: what a mod's Nexus page says it needs.

The v1 REST API that MO2 itself speaks does not carry requirements at all -
its mod record is name, category, version and counters.  The v2 GraphQL API
does, it needs no API key and no OAuth for this, and it bills against a
separate quota from v1, so asking costs nothing the rest of the plugin was
going to spend.

What comes back is ``modRequirements.nexusRequirements``: the list under
"Requirements" on the mod page, each entry with the author's own note.  On
the 913-mod test list, 738 mods carry a Nexus id, 512 of them list at least
one requirement, and 988 of those requirements point at another mod that is
also installed - four of the eight conflicts the sorter could not settle on
its own are answered outright.

Two limits worth stating plainly.  A mod using Nexus's newer per-file
requirements returns nothing here: "Caesia Ostim - Sequenced" lists three
requirements on its page and none of them are in the API, because they hang
off the file rather than the mod.  And a requirement is a statement about
installation, not about override order - "Heavy Armory - Glaive Addons"
requires "Heavy Armory - New Weapons" and is still the one that should lose.
So these edges rank below a user's own decision, and the dialog still gets
the last word.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.nexusmods.com/v2/graphql"
BATCH = 20                      # mods per request; the server accepts aliases
USER_AGENT = "MO2-DAG-Sorter/1.0"


def _post(query: str, timeout: int = 30) -> dict:
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.load(fh)


def game_id(domain: str) -> int | None:
    try:
        payload = _post('{{ game(domainName: "{}") {{ id }} }}'.format(domain))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    game = (payload.get("data") or {}).get("game") or {}
    return game.get("id")


class RequirementCache:
    """{mod id: [(required mod id, note)]}, kept next to the tier cache.

    Requirements change about as often as a mod does, so this is written once
    and reused.  A mod that has been asked and has none is stored as an empty
    list rather than left out, so it is not asked again every run.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.needs: dict[int, list] = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for key, value in (raw.get("needs") or {}).items():
                self.needs[int(key)] = [(int(a), b) for a, b in value]
        except (OSError, ValueError, TypeError):
            pass

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"needs": {str(k): v for k, v in self.needs.items()}},
                          fh, indent=1)
        except OSError:
            pass


def fetch(mod_ids, cache: RequirementCache, game: int,
          progress=None) -> tuple[dict[int, list], str]:
    """Fill the cache for any id it does not hold. Returns (needs, report).

    Offline is not an error: whatever the cache already knows is returned and
    the sort goes ahead without the rest, exactly as it does when the tier
    lookup cannot reach Nexus.
    """
    missing = [i for i in dict.fromkeys(mod_ids) if i not in cache.needs]
    if not missing:
        return cache.needs, "requirements from cache ({} mods)".format(
            len(cache.needs))

    asked = 0
    for start in range(0, len(missing), BATCH):
        chunk = missing[start:start + BATCH]
        query = "{{ {} }}".format(" ".join(
            'm{0}: mod(modId: {0}, gameId: {1}) {{ modRequirements {{ '
            'nexusRequirements {{ nodes {{ modId notes }} }} }} }}'.format(
                mod, game)
            for mod in chunk))
        try:
            data = (_post(query).get("data") or {})
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            break
        for mod in chunk:
            entry = data.get("m{}".format(mod))
            if entry is None:
                continue
            nodes = ((entry.get("modRequirements") or {}).get(
                "nexusRequirements") or {}).get("nodes") or []
            cache.needs[mod] = [(int(n["modId"]), n.get("notes") or "")
                                for n in nodes]
        asked += len(chunk)
        if progress is not None and not progress(asked, len(missing), ""):
            break
        time.sleep(0.2)

    cache.save()
    return cache.needs, "requirements: {} looked up, {} cached".format(
        asked, len(cache.needs))
