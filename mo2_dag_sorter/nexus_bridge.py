"""Component C, inside MO2: borrow the organizer's own Nexus connection.

MO2 already holds a Nexus API key and already knows how to spend it - it
queues requests, honours the rate limit, and keeps the key out of plugin
reach.  ``IOrganizer.createNexusBridge()`` hands a plugin that same pipe, so
there is no reason for this one to ask the user for a second copy of a key
they have already entered.

The bridge is asynchronous, which is the whole cost of using it:

    bridge.requestDescription(game, mod_id, user_data)
    bridge.descriptionAvailable(game, mod_id, user_data, result) -> slot
    bridge.requestFailed(game, mod_id, file_id, user_data, code, message)

so it needs a running event loop, which means it works in MO2 and not in the
offline command-line runner.  ``nexus.resolve`` therefore stays: same cache,
same output, for use outside MO2 or when MO2 has no key.
"""

from __future__ import annotations

from PyQt6.QtCore import QEventLoop, QObject, QTimer

# How many requests to keep in flight. MO2 queues and throttles internally, so
# this is not about the rate limit - it is so that cancelling is responsive
# and a stalled request cannot strand the whole run.
WINDOW = 12
# Give up on a single mod after this long. A dead page never answers at all,
# on either signal.
PER_MOD_MS = 15000


class BridgeResolver(QObject):
    """Resolve {mod id: category id} through MO2's own Nexus connection."""

    def __init__(self, organizer, cache, game_name: str,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cache = cache
        self.game = game_name
        self.bridge = organizer.createNexusBridge()
        self.bridge.descriptionAvailable.connect(self._available)
        self.bridge.requestFailed.connect(self._failed)
        self._pending: set[int] = set()
        self._todo: list[int] = []
        self._loop: QEventLoop | None = None
        self._progress = None
        self.fetched = 0
        self.failed = 0
        self.cancelled = False

    # -- slots -----------------------------------------------------------
    def _available(self, game, mod_id, user_data, result) -> None:
        if mod_id not in self._pending:
            return                       # somebody else's request
        self._pending.discard(mod_id)
        category = None
        name = ""
        if isinstance(result, dict):
            raw = result.get("category_id")
            if isinstance(raw, (int, str)) and str(raw).isdigit():
                category = int(raw)
            name = str(result.get("name") or "")
        self.cache.put(mod_id, category, name)
        self.fetched += 1
        self._pump()

    def _failed(self, game, mod_id, file_id, user_data, code, message) -> None:
        if mod_id not in self._pending:
            return
        self._pending.discard(mod_id)
        # Remembered as a miss so a hidden or deleted page is not re-requested
        # on every future run.
        self.cache.put(mod_id, None)
        self.failed += 1
        self._pump()

    # -- the run ---------------------------------------------------------
    def _pump(self) -> None:
        while self._todo and len(self._pending) < WINDOW:
            mod_id = self._todo.pop(0)
            self._pending.add(mod_id)
            self.bridge.requestDescription(self.game, mod_id, None)
        if self._progress is not None:
            if self._progress.wasCanceled():
                self.cancelled = True
                self._todo.clear()
            else:
                self._progress.setValue(self.fetched + self.failed)
        if not self._todo and not self._pending and self._loop is not None:
            self._loop.quit()

    def resolve(self, mod_ids, progress=None) -> tuple[dict, str]:
        """({mod id: category id}, one-line report). Blocks on a local loop."""
        wanted = sorted({i for i in mod_ids if i})
        self._todo = [i for i in wanted if not self.cache.known(i)]
        already = len(wanted) - len(self._todo)
        if not self._todo:
            return dict(self.cache.categories), (
                "Nexus: {} from cache, nothing to fetch".format(already))

        self._progress = progress
        if progress is not None:
            progress.setMaximum(len(self._todo))
        self._loop = QEventLoop()
        # A page that answers on neither signal would otherwise hold the loop
        # open for ever, so the whole run is bounded as well.
        QTimer.singleShot(PER_MOD_MS * max(1, len(self._todo) // WINDOW + 1),
                          self._loop.quit)
        self._pump()
        if self._todo or self._pending:
            self._loop.exec()
        self._loop = None
        self.cache.save()

        report = "Nexus (via MO2's own key): {} cached, {} fetched, {} " \
                 "unresolved".format(already, self.fetched, self.failed)
        if self.cancelled:
            report += " - cancelled"
        elif self._pending:
            report += " - {} never answered".format(len(self._pending))
        return dict(self.cache.categories), report
