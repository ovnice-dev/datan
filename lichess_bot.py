# bot/lichess_bot.py
# NovaBot – Connexion Lichess (robuste au moment de l'import)
# Ce fichier est conçu pour être importable même si berserk ou d'autres
# dépendances ne sont pas installées. Les imports lourds sont différés.

from pathlib import Path
import traceback
import subprocess as _subprocess
import threading
import chess
from typing import Optional

# ---------------------------
# Patch diagnostic / robustification Path->str
# ---------------------------
def _to_str_if_path(x):
    if isinstance(x, Path):
        return str(x)
    if isinstance(x, (list, tuple)):
        return type(x)(_to_str_if_path(i) for i in x)
    return x

def safe_join(sep, seq):
    return sep.join(str(x) for x in seq)

# Wrappers pour subprocess (convertissent Path en str)
def _subprocess_run_wrapper(*p_args, **p_kwargs):
    if p_args:
        cmd = p_args[0]
        if isinstance(cmd, (list, tuple)):
            cmd = [str(x) if isinstance(x, Path) else x for x in cmd]
            p_args = (cmd,) + p_args[1:]
    if "cwd" in p_kwargs and isinstance(p_kwargs["cwd"], Path):
        p_kwargs["cwd"] = str(p_kwargs["cwd"])
    return _subprocess.run(*p_args, **p_kwargs)

def _subprocess_popen_wrapper(*p_args, **p_kwargs):
    if p_args:
        cmd = p_args[0]
        if isinstance(cmd, (list, tuple)):
            cmd = [str(x) if isinstance(x, Path) else x for x in cmd]
            p_args = (cmd,) + p_args[1:]
    if "cwd" in p_kwargs and isinstance(p_kwargs["cwd"], Path):
        p_kwargs["cwd"] = str(p_kwargs["cwd"])
    return _subprocess.Popen(*p_args, **p_kwargs)

# Remplace localement subprocess.run / Popen
_subprocess.run = _subprocess_run_wrapper
_subprocess.Popen = _subprocess_popen_wrapper

def diagnose_exceptions(func):
    """Décorateur : attrape l'exception, logge la trace et affiche le premier Path trouvé."""
    def _wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[NovaBot DIAG] Exception interceptée dans {func.__name__}: {e}")
            print(tb)

            def find_path(obj, path=[]):
                if isinstance(obj, Path):
                    return path + [obj]
                if isinstance(obj, (list, tuple)):
                    for i, it in enumerate(obj):
                        res = find_path(it, path + [f"[{i}]"])
                        if res:
                            return res
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        res = find_path(v, path + [f"[{k}]"])
                        if res:
                            return res
                return None

            for i, a in enumerate(args):
                res = find_path(a, [f"arg[{i}]"])
                if res:
                    print("[NovaBot DIAG] Path trouvé dans args:", res)
                    break
            for k, v in kwargs.items():
                res = find_path(v, [f"kw[{k}]"])
                if res:
                    print("[NovaBot DIAG] Path trouvé dans kwargs:", res)
                    break

            raise
    _wrapped.__name__ = func.__name__
    return _wrapped

# ---------------------------
# Classe LichessBot (import safe)
# ---------------------------
class LichessBot:
    """
    Classe LichessBot.
    - Import de berserk et initialisation différés dans __init__ pour que le module
      soit importable même si berserk n'est pas installé.
    - Si l'initialisation échoue, la classe reste importable mais l'instance lèvera.
    """

    def __init__(self, token: str):
        # Import différé pour éviter d'échouer à l'import du module
        try:
            import berserk  # import local
        except Exception as e:
            # Rendre l'erreur explicite mais ne pas empêcher l'import du module
            raise RuntimeError(f"berserk non disponible : {e}")

        # Maintenant que berserk est disponible, on peut initialiser la session
        try:
            session = berserk.TokenSession(token)
            self.client = berserk.Client(session=session)
            self.account = self.client.account.get()
            self.bot_id = self.account.get("id", "")
            self.active_games: dict = {}
            # state est importé tardivement pour éviter side-effects à l'import
            try:
                import bot.state as state
                state.set_online(self.bot_id)
            except Exception:
                pass
            print(f"[NovaBot] ✓ Connecté : {self.bot_id}")
        except Exception as e:
            # Fournir un message clair pour le debug
            raise RuntimeError(f"Erreur initialisation LichessBot: {e}")

    def _accept_challenge(self, challenge: dict) -> bool:
        try:
            from config import ACCEPT_VARIANTS, ACCEPT_TIME_CTRL
        except Exception:
            ACCEPT_VARIANTS = ("standard",)
            ACCEPT_TIME_CTRL = ("bullet", "blitz", "rapid", "classical")

        variant = challenge.get("variant", {}).get("key", "standard")
        speed = challenge.get("speed", "")
        challenger = challenge.get("challenger", {}).get("id", "?")
        if variant not in ACCEPT_VARIANTS:
            return False
        if speed not in ACCEPT_TIME_CTRL:
            return False
        print(f"[NovaBot] ✓ Défi accepté de {challenger} ({speed})")
        return True

    @diagnose_exceptions
    def _play_game(self, game_id: str):
        # Importer GameHandler ici pour éviter import au niveau module
        from bot.game_handler import GameHandler

        print(f"[NovaBot] ↳ Partie {game_id}")
        handler: Optional[GameHandler] = None
        try:
            stream = self.client.bots.stream_game_state(game_id)
            prev_moves_count = 0

            for event in stream:
                etype = event.get("type", "")

                if etype == "gameFull":
                    white_id = event.get("white", {}).get("id", "")
                    bot_color = chess.WHITE if white_id == self.bot_id else chess.BLACK
                    handler = GameHandler(self.client, game_id, bot_color)
                    self.active_games[game_id] = handler

                    opp_key = "black" if bot_color == chess.WHITE else "white"
                    opp = event.get(opp_key, {})
                    handler.set_opponent(
                        opp.get("name", opp.get("id", "?")),
                        opp.get("rating", 0),
                    )

                    state_data = event.get("state", {})
                    moves_str = state_data.get("moves", "")
                    handler.sync_moves(moves_str)
                    prev_moves_count = len(moves_str.split()) if moves_str else 0
                    self._try_move(handler, state_data, None)

                elif etype == "gameState" and handler:
                    status = event.get("status", "started")
                    if status not in ("started", "created"):
                        print(f"[NovaBot] Partie {game_id} : {status}")
                        break

                    moves_str = event.get("moves", "")
                    moves_list = moves_str.split() if moves_str else []
                    handler.sync_moves(moves_str)

                    last_opp = None
                    if len(moves_list) > prev_moves_count:
                        try:
                            last_opp = chess.Move.from_uci(moves_list[-1])
                        except Exception:
                            last_opp = None

                    prev_moves_count = len(moves_list)
                    self._try_move(handler, event, last_opp)

        except Exception as e:
            print(f"[NovaBot] Erreur partie {game_id} : {e}")
            raise
        finally:
            if handler:
                try:
                    handler.close()
                except Exception:
                    pass
            self.active_games.pop(game_id, None)
            print(f"[NovaBot] Partie {game_id} fermée.")

    def _try_move(self, handler, state_data: dict, last_opp: Optional[chess.Move]):
        # Si handler n'est pas prêt, on sort
        if handler is None:
            return
        try:
            if not handler.should_play() or handler.board.is_game_over():
                return
        except Exception:
            return

        def _ms(v) -> int:
            if hasattr(v, 'total_seconds'):
                return int(v.total_seconds() * 1000)
            try:
                return int(v)
            except Exception:
                return 60_000

        ck = "wtime" if handler.bot_color == chess.WHITE else "btime"
        ok = "btime" if handler.bot_color == chess.WHITE else "wtime"
        ik = "winc" if handler.bot_color == chess.WHITE else "binc"

        handler.make_move(
            _ms(state_data.get(ck, 60_000)),
            _ms(state_data.get(ok, 60_000)),
            _ms(state_data.get(ik, 0)),
            last_opp,
        )

    def run(self, stop_event=None):
        print("[NovaBot] En attente de défis…")
        try:
            for event in self.client.bots.stream_incoming_events():
                if stop_event and stop_event.is_set():
                    print("[NovaBot] Arrêt demandé.")
                    break
                etype = event.get("type", "")
                if etype == "challenge":
                    challenge = event.get("challenge", {})
                    cid = challenge.get("id", "")
                    if self._accept_challenge(challenge):
                        try:
                            self.client.challenges.accept(cid)
                        except Exception:
                            pass
                    else:
                        try:
                            self.client.challenges.decline(cid)
                        except Exception:
                            pass
                elif etype == "gameStart":
                    gid = event.get("game", {}).get("id", "")
                    if gid and gid not in self.active_games:
                        threading.Thread(target=self._play_game, args=(gid,), daemon=True).start()
                elif etype == "gameFinish":
                    gid = event.get("game", {}).get("id", "")
                    print(f"[NovaBot] ✓ Terminée : {gid}")
        except Exception as e:
            print(f"[NovaBot] Erreur run: {e}")
            raise
