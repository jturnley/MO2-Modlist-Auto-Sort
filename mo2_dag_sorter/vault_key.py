"""Ask the Nexus API Key Vault for a key, if it is installed.

The vault keeps one key for every plugin, sealed as strongly as the
platform allows, instead of each plugin storing its own copy in plain
text in ModOrganizer.ini.  It is an optional dependency in the fullest sense: not
installed is the normal case, and everything here answers "" for it.

The sorter's own `nexus_api_key` setting still works and still wins if it
has something in it, because a user who set it deliberately should not
have it silently ignored.  The vault's dialog offers to move it across and
blank it, which is the path out of storing a credential in the ini.
"""

from __future__ import annotations

# The name the vault's dialog shows for this plugin. Shared with
# `nexus_api`, which asks the same vault for a client: this plugin is one
# caller, so it gets one row in that list and one allow/deny switch. Two
# names for one plugin would mean denying one and leaving the other
# working, which reads as the deny not having taken.
REQUESTER = "MO2 Modlist Auto Sort"


def key(organizer) -> str:
    """The shared key, or "" if there is no vault or nothing in it."""
    try:
        from nexus_key_vault import api
    except Exception:
        # Not installed. The overwhelmingly common case, and not a fault.
        return ""
    try:
        return api.key(organizer, REQUESTER)
    except Exception:
        # A vault that is broken, locked to another Windows account, or a
        # version whose API has moved on must not stop a sort.
        return ""


def installed() -> bool:
    try:
        import nexus_key_vault  # noqa: F401
    except Exception:
        return False
    return True
