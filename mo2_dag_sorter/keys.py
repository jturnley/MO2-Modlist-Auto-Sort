"""Talking about an API key without repeating it.

Split out from :mod:`key_ui` so it can be tested without Qt, and because
this is the part worth being sure of: a credential that gets echoed back
into a dialog, a log line or an error message has left the one place the
user chose to put it.
"""

from __future__ import annotations

SETTING = "nexus_api_key"
OWNER = "MO2 DAG Sorter"

TAIL = 4


def masked(key: str) -> str:
    """A stored key, described rather than shown.

    Enough to tell two keys apart and to confirm one arrived intact, and
    not enough to use.  Short keys give up their tail as well, so nothing
    is described that is mostly the key itself.
    """
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= TAIL * 2:
        return "{} characters".format(len(key))
    return "{} characters, ending {}".format(len(key), key[-TAIL:])


def reason(exc: Exception) -> str:
    """What went wrong, in words, without quoting the key back.

    ``urllib`` puts the requested URL in the string form of some errors,
    and the key travels in a header rather than the URL, but the rule is
    worth holding to whatever the transport does with it today.
    """
    code = getattr(exc, "code", None)
    if code == 401:
        return "Nexus rejected it (401). Check it was copied in full."
    if code == 403:
        return "Nexus refused it (403)."
    if code == 429:
        return "too many requests for now (429) - wait and try again."
    if code:
        return "Nexus answered {}.".format(code)
    return str(exc) or exc.__class__.__name__
