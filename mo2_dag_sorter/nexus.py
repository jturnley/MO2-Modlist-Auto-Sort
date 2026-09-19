"""Component C: the Nexus Mods metadata bridge.

    GET https://api.nexusmods.com/v1/games/{domain}/mods/{id}.json

One call per mod id, so the cache is not an optimisation but the whole point:
a thousand-mod list would otherwise spend its daily rate limit on the first
run.  Cached entries are kept indefinitely - a mod's category changes about as
often as its name does.

Every failure here is survivable by design.  No API key, no network, a 429, a
mod that has been hidden: all of them end the same way, with an empty result
and the tiering falling back to the file tree.  The sorter never blocks on
this bridge.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API_ROOT = "https://api.nexusmods.com/v1/games"
USER_AGENT = "MO2-DAG-Sorter/1.0"
TIMEOUT = 8.0
# Nexus allows 100 requests/minute; this stays well inside it and still gets
# through a few hundred uncached mods in a single run.
PAUSE = 0.35
# Stop this far short of the hourly allowance rather than spending the last
# of it, so MO2's own Nexus features still work after a sort.
RESERVE = 50


class RateLimitLow(Exception):
    """The hourly allowance is nearly gone; stop before it runs out."""



class NexusCache:
    """{nexus mod id: category id}, persisted as nexus_cache.json.

    Each entry also carries the mod's name from the API response. Nothing
    reads it - it is there so that a human opening the cache to work out why
    something was tiered the way it was sees names instead of a wall of
    integer pairs.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.categories: dict[int, int] = {}
        self.names: dict[int, str] = {}
        # The game's own id -> category name table, fetched once. Cached
        # because it changes about once a year and costs a request.
        self.category_names: dict[int, str] = {}
        self.misses: set[int] = set()
        self._dirty = False
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        for key, value in (raw.get("categories") or {}).items():
            try:
                mod_id = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                try:
                    self.categories[mod_id] = int(value["category_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                self.names[mod_id] = str(value.get("name") or "")
            else:                                  # an older, flat cache file
                try:
                    self.categories[mod_id] = int(value)
                except (TypeError, ValueError):
                    continue
        # Ids that came back 404 or otherwise unusable. Remembered so a
        # deleted mod page is not re-requested on every single run.
        self.misses = {int(i) for i in (raw.get("misses") or [])
                       if str(i).isdigit()}
        for key, value in (raw.get("category_names") or {}).items():
            if str(key).isdigit():
                self.category_names[int(key)] = str(value)

    def save(self) -> None:
        if not self._dirty:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"categories": {
                str(k): {"category_id": v, "name": self.names.get(k, "")}
                for k, v in sorted(self.categories.items())},
                "misses": sorted(self.misses),
                "category_names": {str(k): v for k, v
                                   in sorted(self.category_names.items())}},
                fh, indent=1)
        os.replace(tmp, self.path)
        self._dirty = False

    def put(self, mod_id: int, category_id: int | None,
            name: str = "") -> None:
        if category_id is None:
            self.misses.add(mod_id)
        else:
            self.categories[mod_id] = category_id
            self.names[mod_id] = name
            self.misses.discard(mod_id)
        self._dirty = True

    def known(self, mod_id: int) -> bool:
        return mod_id in self.categories or mod_id in self.misses

    def forget(self, mod_id: int) -> None:
        """Drop one mod, so the next resolve asks Nexus about it again.

        For a user who has explicitly asked for these mods to be looked
        at, rather than a sweep that merely needs an answer. Dropping a
        miss matters as much as dropping a hit: a mod that 404'd because
        its page was briefly unavailable would otherwise stay written off
        until the cache expires.
        """
        if mod_id in self.categories or mod_id in self.misses:
            self.categories.pop(mod_id, None)
            self.names.pop(mod_id, None)
            self.misses.discard(mod_id)
            self._dirty = True


def _request(url: str, api_key: str):
    """(payload, hourly requests remaining). -1 when the header is absent."""
    request = urllib.request.Request(url, headers={
        "apikey": api_key, "User-Agent": USER_AGENT,
        "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = json.load(response)
        try:
            remaining = int(response.headers.get("x-rl-hourly-remaining", -1))
        except (TypeError, ValueError):
            remaining = -1
    return payload, remaining


def _via_client(client, path: str):
    """Ask the Extender, raising what this module's own loop expects.

    The loop below was written around urllib's exceptions and is well
    tested; translating here is smaller and safer than rewriting it to
    understand a second error vocabulary.
    """
    try:
        return client.rest(path)
    except Exception as exc:
        status = getattr(exc, "status", None)
        if status:
            raise urllib.error.HTTPError(path, status, str(exc), None, None)
        raise urllib.error.URLError(str(exc))


def fetch_categories(domain: str, api_key: str, client=None) -> dict[int, str]:
    """{category id: name} for a game, from /v1/games/{domain}.json."""
    if client is not None and client.has_key:
        # The Extender keeps this for thirty days - a category table
        # changes about once a year.
        return client.categories(domain)
    payload, _ = _request("{}/{}.json".format(API_ROOT, domain), api_key)
    out: dict[int, str] = {}
    for entry in payload.get("categories") or ():
        try:
            out[int(entry["category_id"])] = str(entry["name"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _fetch(domain: str, mod_id: int, api_key: str,
           client=None) -> tuple[int | None, str]:
    if client is not None:
        payload = _via_client(
            client, "games/{}/mods/{}.json".format(domain, mod_id))
        remaining = client.remaining()
        remaining = -1 if remaining is None else remaining
    else:
        url = "{}/{}/mods/{}.json".format(API_ROOT, domain, mod_id)
        payload, remaining = _request(url, api_key)
    if 0 <= remaining < RESERVE:
        raise RateLimitLow(remaining)
    value = payload.get("category_id")
    category = (int(value) if isinstance(value, (int, str))
                and str(value).isdigit() else None)
    return category, str(payload.get("name") or "")


def resolve(mod_ids, cache: NexusCache, api_key: str | None,
            domain: str = "skyrimspecialedition",
            progress=None,
            client=None) -> tuple[dict[int, int], dict[int, str], str]:
    """({mod id: category id}, a one-line report of how it went).

    Returns whatever the cache already holds even when the network half never
    runs, which is what makes an offline sort produce the same answer as an
    online one for every mod seen before.
    """
    wanted = sorted({i for i in mod_ids if i})
    # The Extender's client carries a key of its own, so having one is
    # enough even when nothing was passed in here.
    if client is not None and not client.has_key:
        client = None
    if not api_key and client is None:
        return dict(cache.categories), dict(cache.category_names), (
            "no Nexus API key set - tiers from cache ({} mods) and file trees"
            .format(len(cache.categories)))

    if not cache.category_names:
        try:
            cache.category_names = fetch_categories(domain, api_key, client)
            cache._dirty = bool(cache.category_names)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            pass

    todo = [i for i in wanted if not cache.known(i)]
    fetched = failed = 0
    stopped = ""
    for n, mod_id in enumerate(todo):
        try:
            cache.put(mod_id, *_fetch(domain, mod_id, api_key, client))
            fetched += 1
        except RateLimitLow as exc:
            stopped = "only {} requests remain this hour".format(exc.args[0])
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                stopped = "the API key was rejected"
                break
            if exc.code == 429:
                stopped = "the API rate limit was reached"
                break
            cache.put(mod_id, None)          # 404: the page is gone
            failed += 1
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            failed += 1
            stopped = "the network is unreachable"
            break
        if progress is not None and not progress(n, len(todo), str(mod_id)):
            stopped = "cancelled"
            break
        time.sleep(PAUSE)

    cache.save()
    report = "Nexus: {} cached, {} fetched, {} unresolved".format(
        len(cache.categories) - fetched, fetched, failed)
    if stopped:
        report += " - stopped early because {}".format(stopped)
    return dict(cache.categories), dict(cache.category_names), report


def read_local_categories(mo2_root: str) -> dict[int, str]:
    """{category id: name} from MO2's own nexuscatmap.dat.

    MO2 writes this file when it last talked to Nexus, and it holds exactly
    the table the game endpoint returns - ``mo2 id|category name|nexus id``,
    one per line, plain text.  So the id-to-name half of the tiering needs no
    API key at all, which matters because that half is the part the hand
    written tier table depends on being right.
    """
    path = os.path.join(mo2_root, "nexuscatmap.dat")
    out: dict[int, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.strip().split("|")
                if len(parts) < 3 or not parts[2].isdigit():
                    continue
                out[int(parts[2])] = parts[1]
    except OSError:
        return {}
    return out


def read_mo2_categories(mo2_root: str) -> dict[int, str]:
    """{MO2 category id: name} from the same nexuscatmap.dat.

    The other half of that file.  A mod installed from a local archive has
    no Nexus id at all - ``modid=0`` - but MO2 still files it under a
    category the user can see in the left pane, and that category is written
    into meta.ini using MO2's own numbering.  Reading it is the difference
    between tiering such a mod on its file tree and tiering it on what it
    actually is.
    """
    path = os.path.join(mo2_root, "nexuscatmap.dat")
    out: dict[int, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.strip().split("|")
                if len(parts) < 2 or not parts[0].isdigit():
                    continue
                out[int(parts[0])] = parts[1]
    except OSError:
        return {}
    return out
