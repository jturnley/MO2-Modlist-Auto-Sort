"""Where the sorter gets its Nexus connection.

The MO2 Nexus API Extender holds one key for every plugin, encrypted, and
hands out a client that already knows v1, v2 and v3 and caches what comes
back.  Going through it means the sorter is not maintaining a second HTTP
layer, a second cache, and a second copy of the rules about not draining
someone's quota - and it means a mod this plugin looked up is already
paid for when another plugin asks about the same one.

The Extender is a requirement, not an optional extra.  But a requirement
is a thing users skip, so nothing here raises when it is missing: the
sorter falls back to its own transport, which is what it used before, and
says so in the report rather than failing a sort over it.

One thing worth getting right: v2's public queries need no credential.
A client is asked for with ``require_key=False`` so requirements still
resolve for a user who has installed the Extender and not put a key in
it.  Only the v1 category lookup needs one.
"""

from __future__ import annotations

REQUESTER = "MO2 Modlist Auto Sort"


def installed() -> bool:
    try:
        import nexus_key_vault  # noqa: F401
    except Exception:
        return False
    return True


def connect(organizer):
    """A client from the Extender, or None to use our own transport.

    Never raises.  A missing, broken or newer-than-expected Extender is
    a reason to do the work the old way, not a reason to stop.
    """
    try:
        from nexus_key_vault import api
    except Exception:
        return None
    try:
        return api.client(organizer, REQUESTER, require_key=False)
    except Exception:
        return None


def connect_offline(cache_dir: str, api_key: str | None = None):
    """A client for the command-line runner, which has no IOrganizer.

    Points at the same shared cache file the plugin uses, so a sort run
    from a terminal and one run inside MO2 warm the same cache instead of
    each paying for the other's lookups.  The key has to be passed in:
    reading the vault needs MO2 to say where its plugin data lives.
    """
    try:
        from nexus_key_vault import api, cache as cache_mod
    except Exception:
        return None
    try:
        import os
        path = os.path.join(cache_dir, "nexus_key_vault", cache_mod.FILENAME)
        return api.NexusClient(api_key or "", user_agent="MO2-Auto-Sort/1.0",
                               cache=api.Cache(path))
    except Exception:
        return None


def finish(client) -> None:
    """Write the shared cache out at the end of a run."""
    try:
        if client is not None and getattr(client, "cache", None) is not None:
            client.cache.save()
    except Exception:
        # A cache that will not save costs the next run a cold start and
        # nothing else.
        pass


def status(client) -> str:
    """One line for the report, saying where the metadata came from."""
    if client is None:
        return ("Nexus API Extender not installed - using this plugin's own "
                "connection. Installing it shares one encrypted key and one "
                "response cache across your plugins.")
    where = "with your stored key" if client.has_key else "without a key"
    left = client.remaining()
    quota = "" if left is None else ", {} v1 requests left this hour".format(left)
    return "Nexus API Extender {}{}.".format(where, quota)
