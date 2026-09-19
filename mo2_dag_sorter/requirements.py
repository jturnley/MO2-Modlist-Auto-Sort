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
GIVE_UP = 3                     # consecutive failed batches before stopping
MAX_AGE = 7 * 86400.0           # how long a stored answer is trusted
USER_AGENT = "MO2-DAG-Sorter/1.0"


class Unavailable(Exception):
    """A batch did not come back. Carries why, in words a user can read.

    This used to be flattened into `URLError(str(exc))`, which the fetch
    loop caught alongside genuine network errors and turned into a silent
    stop. The reason never reached the report, so a sweep that fetched
    nothing looked exactly like a sweep that had nothing to fetch.
    """


def _post(query: str, timeout: int = 30, client=None) -> dict:
    """One GraphQL call, through the Extender when it is there.

    v2 needs no credential for these queries, so this path works whether
    or not a key is stored - what the Extender adds is one shared cache
    instead of a second private one.
    """
    if client is not None:
        try:
            return {"data": client.graphql(query)}
        except Exception as exc:
            raise Unavailable("{}: {}".format(type(exc).__name__, exc))
    try:
        return _post_direct(query, timeout)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        raise Unavailable("{}: {}".format(type(exc).__name__, exc))


def _post_direct(query: str, timeout: int = 30) -> dict:
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.load(fh)


def game_id(domain: str, client=None) -> int | None:
    if client is not None:
        try:
            return client.game_id(domain)
        except Exception:
            return None
    try:
        payload = _post('{{ game(domainName: "{}") {{ id }} }}'.format(domain))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    game = (payload.get("data") or {}).get("game") or {}
    return game.get("id")


class RequirementCache:
    """{mod id: [(required mod id, note)]}, kept next to the tier cache.

    A mod that has been asked and has none is stored as an empty list
    rather than left out, so it is not asked again every run.

    Each entry carries the time it was fetched, and anything older than
    ``MAX_AGE`` is asked again.  Requirements are the part of a mod page an
    author edits *after* release - a patch gets added, a dependency is
    dropped - so an answer cached permanently would mean the sorter kept
    acting on a page as it looked the first time it was ever seen.  There
    is no cheaper way to find out: nothing in the API says when a mod's
    requirements last changed, so the only question available is the whole
    question.

    A week is chosen to be quiet rather than current.  These go over v2
    GraphQL, which needs no key and bills against a different quota from
    everything else here, so the re-ask costs one batched request per
    twenty mods and nothing the rest of the plugin was going to spend.
    Right-clicking a mod re-asks about it immediately, which is the answer
    when a week is too long to wait.
    """

    def __init__(self, path: str, max_age: float = MAX_AGE) -> None:
        self.path = path
        self.max_age = max_age
        self.needs: dict[int, list] = {}
        # Kept beside `needs` rather than inside it so `needs` stays the
        # plain {id: list} the rest of the plugin reads.
        self.fetched: dict[int, float] = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for key, value in (raw.get("needs") or {}).items():
                self.needs[int(key)] = [(int(a), b) for a, b in value]
            for key, value in (raw.get("fetched") or {}).items():
                self.fetched[int(key)] = float(value)
        except (OSError, ValueError, TypeError):
            pass

    def stale(self, mod_id: int) -> bool:
        """Should this mod be asked about - missing, or old enough?

        An entry with no timestamp was written before this cache stamped
        anything, so its age is unknown and could be a year.  Unknown is
        treated as stale: the first sort after upgrading re-asks, which is
        the point of adding the stamp at all.
        """
        if mod_id not in self.needs:
            return True
        at = self.fetched.get(mod_id)
        if at is None:
            return True
        return (time.time() - at) > self.max_age

    def put(self, mod_id: int, needs: list) -> None:
        """Store an answer and the moment it arrived."""
        self.needs[mod_id] = needs
        self.fetched[mod_id] = time.time()

    def forget(self, mod_id: int) -> None:
        """Drop one mod's requirements, so they are fetched again.

        Requirements are the half of the data an author edits after
        release, so this is the entry most worth being able to re-ask
        for.
        """
        self.needs.pop(mod_id, None)
        self.fetched.pop(mod_id, None)

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"needs": {str(k): v
                                     for k, v in self.needs.items()},
                           # Truncated, never rounded: a stamp that
                           # rounded up would read as newer than it is.
                           "fetched": {str(k): int(v)
                                       for k, v in self.fetched.items()}},
                          fh, indent=1)
        except OSError:
            pass


def fetch(mod_ids, cache: RequirementCache, game: int,
          progress=None, client=None) -> tuple[dict[int, list], str]:
    """Fill the cache for any id it does not hold. Returns (needs, report).

    Offline is not an error: whatever the cache already knows is returned and
    the sort goes ahead without the rest, exactly as it does when the tier
    lookup cannot reach Nexus.
    """
    wanted = list(dict.fromkeys(mod_ids))
    missing = [i for i in wanted if cache.stale(i)]
    # Told apart only for the report: a mod never asked about reads as new,
    # one whose answer has aged out reads as a re-check, and a user looking
    # at "looked up 40" should be able to tell which happened.
    aged = sum(1 for i in missing if i in cache.needs)
    if not missing:
        return cache.needs, "requirements from cache ({} mods)".format(
            len(cache.needs))

    asked = 0
    failures = 0                # consecutive, reset by any batch that works
    reason = ""                 # the first failure's explanation, for the report
    for start in range(0, len(missing), BATCH):
        chunk = missing[start:start + BATCH]
        query = "{{ {} }}".format(" ".join(
            'm{0}: mod(modId: {0}, gameId: {1}) {{ modRequirements {{ '
            'nexusRequirements {{ nodes {{ modId notes }} }} }} }}'.format(
                mod, game)
            for mod in chunk))
        try:
            data = (_post(query, client=client).get("data") or {})
        except Unavailable as exc:
            # One bad batch is not a reason to abandon the other 300. It
            # was: the first failure broke the loop, so a single hiccup
            # 19 batches in cost the whole rest of the sweep and said
            # nothing about why. Keep going, and give up only once it is
            # clear the whole endpoint is gone rather than one request.
            failures += 1
            reason = reason or str(exc)
            if failures >= GIVE_UP:
                break
            time.sleep(0.2)
            continue
        failures = 0
        for mod in chunk:
            entry = data.get("m{}".format(mod))
            if entry is None:
                continue
            nodes = ((entry.get("modRequirements") or {}).get(
                "nexusRequirements") or {}).get("nodes") or []
            cache.put(mod, [(int(n["modId"]), n.get("notes") or "")
                            for n in nodes])
        asked += len(chunk)
        if progress is not None and not progress(asked, len(missing), ""):
            break
        time.sleep(0.2)

    cache.save()
    report = "requirements: {} looked up, {} cached".format(
        asked, len(cache.needs))
    if aged:
        report += " ({} re-checked after a week)".format(aged)
    if reason:
        short = reason if len(reason) <= 120 else reason[:117] + "..."
        missed = len(missing) - asked
        report += "; {} not looked up - {}".format(missed, short)
    return cache.needs, report
