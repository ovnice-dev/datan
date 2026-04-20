# =============================================================
#  NovaBot – État global partagé  (bot ↔ control panel)
# =============================================================
"""
Ce module expose un dictionnaire thread-safe qui contient
l'état en temps réel du bot (parties actives, stats moteur).
Le control panel le lit pour afficher la vue "Partie en direct".
"""
import threading
import time

_lock  = threading.Lock()

_state = {
    "online":  False,
    "bot_id":  None,
    "games":   {},    # game_id → dict avec les infos de la partie
}

# ─────────────────────────────────────────────────────────────
#  Écriture
# ─────────────────────────────────────────────────────────────
def set_online(bot_id: str):
    with _lock:
        _state["online"] = True
        _state["bot_id"] = bot_id

def set_offline():
    with _lock:
        _state["online"] = False

def update_game(game_id: str, data: dict):
    """Met à jour les infos d'une partie (appelé depuis game_handler)."""
    with _lock:
        if game_id not in _state["games"]:
            _state["games"][game_id] = {"game_id": game_id, "updated": 0}
        _state["games"][game_id].update(data)
        _state["games"][game_id]["updated"] = time.time()

def remove_game(game_id: str):
    with _lock:
        _state["games"].pop(game_id, None)

# ─────────────────────────────────────────────────────────────
#  Lecture
# ─────────────────────────────────────────────────────────────
def get_snapshot() -> dict:
    """Retourne une copie de l'état (sans lock long)."""
    import copy
    with _lock:
        return copy.deepcopy(_state)

def is_online() -> bool:
    with _lock:
        return _state["online"]

def get_active_games() -> list:
    with _lock:
        return list(_state["games"].values())
