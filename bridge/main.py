"""StS2 Advisor — entry point and poll loop.

Polls the STS2MCP bridge (GET only), detects owned decision screens, debounces
and de-dupes per decision, grounds the prompt with authoritative card/relic
text + the running thesis, and fires the model on a worker thread. Writes a
heartbeat the overlay watches so it opens/closes with the game.

Run:  python -m bridge.main
"""
from __future__ import annotations

import json
import time

from . import client as client_mod
from . import grounding, screens, signature, thesis, util, worker
from .model_client import ModelClient


class RunTracker:
    """Detects when a new run has started so the thesis can reset."""

    def __init__(self):
        self._char = None
        self._act = None
        self._floor = None

    @staticmethod
    def _character(state: dict) -> str | None:
        # character lives under player in the live API (run has only act/floor/asc)
        return (state.get("player") or {}).get("character") or \
               (state.get("run") or {}).get("character")

    def run_key(self, state: dict) -> str:
        run = state.get("run") or {}
        return f"{self._character(state)}|asc{run.get('ascension')}"

    def is_new_run(self, state: dict) -> bool:
        run = state.get("run") or {}
        char = self._character(state)
        act = run.get("act")
        floor = run.get("floor")
        new = False
        if char and char != self._char:
            new = True
        elif (self._floor is not None and floor is not None
              and act == 1 and floor <= 1 and self._floor > floor):
            new = True  # floor reset back to act 1 start
        self._char, self._act, self._floor = char, act, floor
        return new


class DeckTracker:
    """Maintains the player's current deck.

    STS2MCP doesn't expose the deck outside combat, so we:
      1. seed it from the character's starter deck at run start, then
      2. overwrite it from the combat piles (hand+draw+discard+exhaust) every
         time we observe a combat — the game's own truth, refreshed each fight.
    Persisted to runtime/deck.json so it survives an advisor restart mid-run.
    Each deck entry is {name, is_upgraded, count}.
    """

    _PILE_KEYS = ("hand", "draw_pile", "discard_pile", "exhaust_pile")

    def __init__(self, starter_decks: dict, persist_path, logger):
        self._starters = starter_decks or {}
        self._path = persist_path
        self._logger = logger
        self._cards: list[dict] = []
        self._source = ""
        self._load()

    # ---- persistence -----------------------------------------------------
    def _load(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._cards = data.get("cards", [])
                self._source = data.get("source", "")
        except (ValueError, OSError):
            pass

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(
                {"source": self._source, "cards": self._cards}, indent=2),
                encoding="utf-8")
        except OSError:
            pass

    # ---- seeding + snapshotting -----------------------------------------
    def seed_starter(self, character: str | None) -> None:
        key = (character or "").replace("The ", "").strip().lower()
        starter = self._starters.get(key)
        if starter:
            self._cards = [{"name": c["name"], "is_upgraded": False,
                            "count": int(c.get("count", 1))} for c in starter]
            self._source = f"starter deck for {key} (assumed; verifies on first combat)"
            self._logger.info("Seeded starter deck for %s (%d cards).",
                              key, sum(c["count"] for c in self._cards))
        else:
            self._cards = []
            self._source = "unknown until first combat observed"
        self._save()

    @staticmethod
    def _pile_len(player: dict, list_key: str, count_key: str) -> int:
        v = player.get(list_key)
        if isinstance(v, list):
            return len(v)
        return int(player.get(count_key) or 0)

    def maybe_snapshot(self, state: dict) -> None:
        player = state.get("player") or {}
        hand = player.get("hand") or []
        draw = player.get("draw_pile") or []
        if not hand and not draw:
            return  # not in combat / no pile data

        # ONLY snapshot at combat start, when the WHOLE deck is in hand + draw and
        # no cards have been played yet. Mid-combat snapshots are unreliable:
        # played Power cards (e.g. Hell Raiser) are consumed and vanish from every
        # pile, so they'd be silently dropped from the deck. At combat start the
        # discard and exhaust piles are empty.
        discard_n = self._pile_len(player, "discard_pile", "discard_pile_count")
        exhaust_n = self._pile_len(player, "exhaust_pile", "exhaust_pile_count")
        if discard_n or exhaust_n:
            return  # mid-combat — deck view is incomplete; keep the start snapshot

        counts: dict[tuple, int] = {}
        total = 0
        for c in list(hand) + list(draw):
            if isinstance(c, dict):
                name, up = c.get("name"), bool(c.get("is_upgraded"))
            else:
                name, up = str(c), False
            if name:
                counts[(name, up)] = counts.get((name, up), 0) + 1
                total += 1
        if total == 0:
            return
        new_cards = [{"name": n, "is_upgraded": u, "count": ct}
                     for (n, u), ct in sorted(counts.items())]
        floor = (state.get("run") or {}).get("floor", "?")
        src = f"observed at combat start (floor {floor}, {total} cards)"
        if new_cards != self._cards:
            self._cards = new_cards
            self._source = src
            self._save()
            self._logger.info("Deck updated at combat start: %d cards (floor %s).",
                              total, floor)
        else:
            self._source = src

    @property
    def cards(self) -> list[dict]:
        return self._cards

    @property
    def source(self) -> str:
        return self._source


def _load_sample(cfg) -> dict:
    path = util.resolve(cfg["dev"]["sample_state_file"])
    return json.loads(path.read_text(encoding="utf-8"))


_WIN_WORDS = ("victor", "triumph", " win", "won", "ascend", "cleared", "complete", "escape")
_LOSS_WORDS = ("died", "death", "defeat", "lost", "slain", "fell", "perish")


def _game_over_message(state: dict) -> str:
    """Build a victory/defeat acknowledgement. STS2MCP gives no win/loss flag, so
    infer from the game_over message text, falling back to HP (you finish a won
    run alive; death is 0 HP)."""
    go = state.get("game_over") or {}
    run = state.get("run") or {}
    player = state.get("player") or {}
    raw = str(go.get("message") or "").lower()
    char = (player.get("character") or "?").replace("The ", "")
    act = run.get("act", "?")
    floor = run.get("floor", "?")
    hp = player.get("hp")

    if any(w in raw for w in _WIN_WORDS):
        won = True
    elif any(w in raw for w in _LOSS_WORDS):
        won = False
    else:
        won = (hp or 0) > 0  # finished alive -> victory

    if won:
        return (f"[ run complete ]\n🏆 VICTORY — {char}! You beat the Spire.\n"
                f"Reached Act {act}. GG — start a new run when ready.")
    return (f"[ run over ]\n💀 Defeated — {char}, Act {act}, Floor {floor}.\n"
            f"{go.get('message') or 'The run has ended.'}")


def main() -> None:
    cfg = util.load_config()
    logger = util.setup_logging(cfg["paths"]["log_file"])
    logger.info("StS2 Advisor starting.")

    dataset = grounding.Dataset.load(cfg["paths"]["data_dir"])
    logger.info("Dataset loaded: %d cards, %d relics (game v%s)",
                len(dataset.cards), len(dataset.relics), dataset.version)
    if not dataset.cards:
        logger.warning("No card data found. Run `python data/fetch_data.py` first; "
                       "advice will fall back to in-state text only.")

    starter_decks = {}
    starter_path = util.resolve(cfg["paths"]["data_dir"]) / "starter_decks.json"
    if starter_path.exists():
        try:
            starter_decks = {k: v for k, v in
                             json.loads(starter_path.read_text(encoding="utf-8")).items()
                             if not k.startswith("_")}
        except (ValueError, OSError):
            logger.warning("Could not read starter_decks.json")

    bridge = client_mod.BridgeClient(
        base_url=cfg["bridge"]["base_url"],
        state_path=cfg["bridge"]["state_path"],
        health_path=cfg["bridge"]["health_path"],
        timeout=cfg["bridge"]["request_timeout_s"],
    )
    model = ModelClient(cfg, logger)
    thesis_store = thesis.ThesisStore(cfg["paths"]["thesis_file"])
    debouncer = signature.Debouncer(cfg["debounce"]["settle_s"])
    tracker = RunTracker()
    deck = DeckTracker(starter_decks, util.resolve("runtime/deck.json"), logger)
    adv_worker = worker.AdviceWorker(
        model, thesis_store, cfg["paths"]["advice_file"], logger)
    adv_worker.start()

    heartbeat_path = util.resolve(cfg["paths"]["heartbeat_file"])
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)

    use_sample = cfg["dev"].get("use_sample_state", False)
    poll = cfg["bridge"]["poll_interval_s"]
    backoff = cfg["bridge"]["reconnect_backoff_s"]
    log_prompts = cfg["dev"].get("log_prompts", False)
    game_over_handled = False

    logger.info("Polling %s%s", bridge.base_url, bridge.state_path)
    try:
        while True:
            # 1. Read state (or sample fixture in dev mode).
            try:
                state = _load_sample(cfg) if use_sample else bridge.get_state()
            except client_mod.BridgeError as exc:
                logger.debug("bridge unreachable: %s", exc)
                # Don't touch heartbeat -> overlay hides itself (game not running).
                time.sleep(backoff)
                continue

            # Bridge is up: heartbeat so the overlay shows.
            heartbeat_path.write_text(str(time.time()), encoding="utf-8")

            # 2. New-run detection -> reset thesis + warm sessions + seed deck.
            if tracker.is_new_run(state):
                logger.info("New run detected -> resetting thesis + seeding deck.")
                thesis_store.reset(tracker.run_key(state))
                model.reset_sessions()
                debouncer.reset()
                deck.seed_starter(RunTracker._character(state))

            # Run end: announce victory/defeat once, then reset for the next run.
            if state.get("state_type") == "game_over" or state.get("game_over"):
                if not game_over_handled:
                    msg = _game_over_message(state)
                    util.resolve(cfg["paths"]["advice_file"]).write_text(
                        msg + "\n", encoding="utf-8")
                    logger.info("Run ended: %s", msg.splitlines()[0])
                    thesis_store.reset()
                    model.reset_sessions()
                    debouncer.reset()
                    game_over_handled = True
                time.sleep(poll)
                continue
            game_over_handled = False

            # Snapshot the deck whenever combat piles are visible (the only time
            # STS2MCP exposes the deck); reused on the next decision screen.
            deck.maybe_snapshot(state)

            # 3. Classify screen.
            kind, is_decision = screens.classify(state)
            if not is_decision:
                # combat / transition / menu -> stay silent.
                time.sleep(poll)
                continue

            # 4. Debounce + de-dupe.
            sig = signature.build(state, kind)
            debouncer.observe(sig)
            if debouncer.should_fire(sig):
                m_kind = screens.model_kind(state, kind)
                alias = model.model_for(m_kind)
                payload = grounding.build_payload(
                    state, kind, dataset, thesis_store.format_for_prompt(),
                    deck_cards=deck.cards, deck_source=deck.source)
                if log_prompts:
                    logger.info("PROMPT [%s -> %s]:\n%s", kind, alias, payload)
                logger.info("Firing advice: %s (model=%s, sig=%s)", kind, alias, sig)
                adv_worker.request(sig, payload, alias, kind)
                debouncer.mark_fired(sig)

            time.sleep(poll)
    except KeyboardInterrupt:
        logger.info("Shutting down (Ctrl-C).")
    finally:
        adv_worker.stop()
        model.shutdown()
        try:
            heartbeat_path.unlink()  # signal overlay to close
        except OSError:
            pass


if __name__ == "__main__":
    main()
