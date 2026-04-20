# =============================================================
# NovaBot – GameHandler (version complète, prête à coller)
# Gestionnaire de partie avec envoi robuste des coups (retries + backoff),
# ponder, latence mesurée et intégration avec engine.search.
# Remplace entièrement bot/game_handler.py par ce fichier.
# =============================================================
import chess
import chess.polyglot
import threading
import time
import traceback
import socket
import inspect
from requests.exceptions import RequestException

from engine.search import (search, search_ponder, abort_ponder, _nodes)
from engine.time_manager import compute_think_time
from engine.position_info import PositionInfo, PHASE_ENDGAME
from engine.opening_book import get_book_move, get_book_stats
import bot.state as state

from config import POLYGLOT_PATH, NETWORK_LATENCY_S, MIN_THINK_TIME


# ---------------------------------------------------------------------
# Robust send_move_with_retry
# - Detects whether client.bots.make_move accepts 'timeout' and uses it only if supported.
# - Retries on transient network errors with exponential backoff.
# - Returns True on success, False on permanent failure.
# ---------------------------------------------------------------------
def _make_move_call(client, game_id: str, uci_move: str, timeout: float | None = None):
    """
    Call client.bots.make_move with or without timeout depending on signature.
    Returns the result or raises the underlying exception.
    """
    make_move = getattr(client.bots, "make_move", None)
    if make_move is None:
        raise AttributeError("client.bots.make_move not found")

    try:
        sig = inspect.signature(make_move)
        if "timeout" in sig.parameters and timeout is not None:
            return make_move(game_id, uci_move, timeout=timeout)
        else:
            return make_move(game_id, uci_move)
    except (ValueError, TypeError):
        # Some callables (C extensions) may not expose a signature; try without timeout first
        try:
            return make_move(game_id, uci_move)
        except TypeError:
            # fallback: try with timeout if provided
            if timeout is not None:
                return make_move(game_id, uci_move, timeout)
            raise


def send_move_with_retry(client, game_id: str, uci_move: str,
                         max_retries: int = 3, base_delay: float = 0.5,
                         timeout: float | None = None) -> bool:
    """
    Robust send with retries and exponential backoff.
    - Detects whether client.bots.make_move accepts 'timeout' and uses it only if supported.
    - Returns True on success, False on permanent failure.
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            _make_move_call(client, game_id, uci_move, timeout=timeout)
            print(f"[NovaBot] ✓ Coup envoyé : {uci_move} (tentative {attempt})")
            return True
        except TypeError as e:
            # Signature mismatch or unexpected kwarg: log and stop retrying
            last_exc = e
            print(f"[NovaBot] Erreur TypeError en envoi (signature inattendue) : {e}")
            print(traceback.format_exc())
            break
        except (RequestException, socket.error, ConnectionAbortedError, ConnectionResetError) as e:
            last_exc = e
            print(f"[NovaBot] Erreur envoi tentative {attempt}/{max_retries} : {e}")
            print(traceback.format_exc())
            # backoff exponentiel + petit jitter
            delay = base_delay * (2 ** (attempt - 1)) + 0.05 * attempt
            time.sleep(delay)
        except Exception as e:
            last_exc = e
            print(f"[NovaBot] Erreur inattendue en envoi de coup: {e}")
            print(traceback.format_exc())
            break

    print(f"[NovaBot] Échec envoi coup {uci_move} après {max_retries} tentatives. Dernière erreur: {last_exc}")
    return False


# =============================================================
# GameHandler
# =============================================================
class GameHandler:
    def __init__(self, client, game_id: str, bot_color: chess.Color):
        self.client    = client
        self.game_id   = game_id
        self.bot_color = bot_color
        self.board     = chess.Board()
        self._lock     = threading.Lock()

        # Pondering
        self._ponder_thread: threading.Thread | None = None
        self._ponder_stop  = threading.Event()
        self._ponder_move : chess.Move | None = None

        # Suivi de la latence réseau (moyenne glissante sur 8 coups)
        self._latency_history: list[float] = []

        state.update_game(game_id, {
            "status": "started",
            "color":  "Blancs" if bot_color == chess.WHITE else "Noirs",
            "eval": 0, "depth": 0, "nps": 0,
            "nodes": 0, "best_move": "—", "fullmove": 1,
            "opponent": "?", "book_move": False,
        })

    # ─────────────────────────────────────────────────────────
    def sync_moves(self, moves_str: str):
        with self._lock:
            self.board.reset()
            if moves_str:
                for uci in moves_str.split():
                    try:
                        mv = chess.Move.from_uci(uci)
                        if mv in self.board.legal_moves:
                            self.board.push(mv)
                        else:
                            print(f"[NovaBot] ⚠ Coup illégal ignoré dans sync: {uci}")
                    except Exception as e:
                        print(f"[NovaBot] ⚠ Coup invalide ignoré: {uci} ({e})")
                        break

    def should_play(self) -> bool:
        return self.board.turn == self.bot_color

    def set_opponent(self, name: str, elo: int):
        state.update_game(self.game_id, {"opponent": f"{name} ({elo})"})

    # ─────────────────────────────────────────────────────────
    # Latence réseau – moyenne glissante
    # ─────────────────────────────────────────────────────────
    def _record_latency(self, latency_s: float):
        """Enregistre une mesure de latence et garde les 8 dernières."""
        self._latency_history.append(latency_s)
        if len(self._latency_history) > 8:
            self._latency_history.pop(0)

    def _estimated_latency(self) -> float:
        """Retourne la latence estimée (moyenne glissante, ou NETWORK_LATENCY_S)."""
        if not self._latency_history:
            return NETWORK_LATENCY_S
        avg = sum(self._latency_history) / len(self._latency_history)
        # Marge de sécurité : 90e centile approximé = moyenne + 30 %
        return avg * 1.30

    # ─────────────────────────────────────────────────────────
    # Pondering
    # ─────────────────────────────────────────────────────────
    def _stop_pondering(self):
        if self._ponder_thread and self._ponder_thread.is_alive():
            abort_ponder()
            self._ponder_stop.set()
            self._ponder_thread.join(timeout=1.0)
            self._ponder_stop.clear()
            self._ponder_thread = None

    def _start_pondering(self, board_after: chess.Board):
        self._stop_pondering()
        self._ponder_move = None
        key   = chess.polyglot.zobrist_hash(board_after)
        from engine.search import _tt_probe
        entry = _tt_probe(key)
        if entry is None or entry.move is None:
            return
        predicted = entry.move
        if predicted not in board_after.legal_moves:
            return
        ponder_board = board_after.copy()
        ponder_board.push(predicted)
        self._ponder_move = predicted
        self._ponder_stop.clear()
        stop_ev = self._ponder_stop
        t = threading.Thread(
            target=lambda: search_ponder(ponder_board, stop_ev),
            daemon=True
        )
        self._ponder_thread = t
        t.start()
        print(f"[NovaBot] ♟ Pondering → {predicted}")

    # ─────────────────────────────────────────────────────────
    # Jouer un coup
    # ─────────────────────────────────────────────────────────
    def make_move(self, my_time_ms: int, opp_time_ms: int,
                  incr_ms: int, last_opp_move: chess.Move | None = None):

        # ── Vérification anti-flag précoce ───────────────────
        # Si on a moins de 2 × MIN_THINK_TIME, jouer immédiatement le premier coup légal.
        if my_time_ms < int((MIN_THINK_TIME * 2 + self._estimated_latency()) * 1000):
            with self._lock:
                board_copy = self.board.copy()
            legal = list(board_copy.legal_moves)
            if legal:
                move = legal[0]
                print(f"[NovaBot] ⚡ EMERGENCY move (low time): {move.uci()}")
                t_send = time.time()
                ok = send_move_with_retry(self.client, self.game_id, move.uci())
                if ok:
                    self._record_latency(time.time() - t_send)
                else:
                    print(f"[NovaBot] Erreur envoi emergency, abandon.")
            return

        ponder_hit = (
            last_opp_move is not None
            and last_opp_move == self._ponder_move
            and self._ponder_thread is not None
            and self._ponder_thread.is_alive()
        )
        self._stop_pondering()

        with self._lock:
            board_copy = self.board.copy()

        if board_copy.is_game_over():
            return

        info       = PositionInfo(board_copy)
        is_endgame = (info.game_phase == PHASE_ENDGAME)
        pos_labels = {0: "Ouverte", 1: "Semi", 2: "Fermée"}

        # ── Livre d'ouvertures ────────────────────────────────
        book_move = get_book_move(board_copy, POLYGLOT_PATH)
        if book_move is not None:
            uci = book_move.uci()
            print(f"\n[NovaBot] 📖 Coup du livre : {uci}")
            state.update_game(self.game_id, {
                "best_move": uci, "eval": 0, "depth": 0,
                "nps": 0, "nodes": 0,
                "fullmove": board_copy.fullmove_number,
                "book_move": True,
            })
            t_send = time.time()
            ok = send_move_with_retry(self.client, self.game_id, uci)
            if ok:
                self._record_latency(time.time() - t_send)
            else:
                print(f"[NovaBot] Abandon envoi livre pour {self.game_id}")
                return
            board_after = board_copy.copy()
            board_after.push(book_move)
            if not board_after.is_game_over():
                self._start_pondering(board_after)
            return

        # ── Calcul du temps ───────────────────────────────────
        estimated_latency = self._estimated_latency()

        think_time = compute_think_time(
            my_time_ms       = my_time_ms,
            opponent_time_ms = opp_time_ms,
            increment_ms     = incr_ms,
            fullmove_number  = board_copy.fullmove_number,
            is_endgame       = is_endgame,
        )

        # Le time_manager déduit déjà NETWORK_LATENCY_S (config),
        # mais on affine avec la latence mesurée en live si elle diffère.
        latency_correction = estimated_latency - NETWORK_LATENCY_S
        think_time = max(think_time - latency_correction, MIN_THINK_TIME)

        if ponder_hit:
            think_time *= 0.80

        color_str = "Blancs" if self.bot_color == chess.WHITE else "Noirs"
        print(f"\n[NovaBot] ── {self.game_id} | {color_str} "
              f"| coup #{board_copy.fullmove_number} "
              f"| {pos_labels[info.pos_type]} "
              f"| think={think_time:.2f}s "
              f"| latency≈{estimated_latency*1000:.0f}ms "
              f"{'| PONDER HIT ✓' if ponder_hit else ''}")

        t0    = time.time()
        move, score, depth = search(board_copy, think_time)
        elapsed = time.time() - t0

        # BUG FIX: validation finale du coup
        if move is None or move not in board_copy.legal_moves:
            legal = list(board_copy.legal_moves)
            if not legal:
                print("[NovaBot] Aucun coup légal.")
                return
            print(f"[NovaBot] ⚠ Coup invalide {move} → premier coup légal")
            move = legal[0]

        nps     = int(_nodes[0] / max(elapsed, 0.001))
        mate_in = None
        if abs(score) >= 89_500:
            mate_in = (90_000 - abs(score) + 1) // 2
            if score < 0: mate_in = -mate_in
        score_str = f"mate {mate_in}" if mate_in else f"{score/100:+.2f}p"
        print(f"[NovaBot] ➤ {move.uci()} "
              f"(eval={score_str} depth={depth} nps={nps:,})")

        state.update_game(self.game_id, {
            "eval": score, "depth": depth, "nps": nps,
            "nodes": _nodes[0], "best_move": move.uci(),
            "fullmove": board_copy.fullmove_number,
            "think_ms": int(elapsed * 1000),
            "time_left": my_time_ms, "book_move": False,
        })

        t_send = time.time()
        ok = send_move_with_retry(self.client, self.game_id, move.uci())
        if ok:
            actual_latency = time.time() - t_send
            self._record_latency(actual_latency)
            print(f"[NovaBot] 📡 Latence envoi: {actual_latency*1000:.0f}ms")
        else:
            print(f"[NovaBot] Abandon envoi coup, fermeture partie {self.game_id}")
            return

        board_after = board_copy.copy()
        board_after.push(move)
        if not board_after.is_game_over():
            self._start_pondering(board_after)

    def close(self):
        self._stop_pondering()
        state.remove_game(self.game_id)
