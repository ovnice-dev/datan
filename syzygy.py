# =============================================================
#  NovaBot – Syzygy Tablebase via API Lichess
#
#  Utilise https://tablebase.lichess.ovh (gratuit, pas de clé)
#  Activé automatiquement quand ≤ 5 pièces sur l'échiquier.
#
#  Retourne le meilleur coup avec résultat parfait (DTZ/DTM).
#  Zéro stockage local — appel HTTP en temps réel.
#
#  Cache en mémoire pour éviter les appels répétés.
# =============================================================
import chess
import threading
import time

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# ─────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────
SYZYGY_URL      = "https://tablebase.lichess.ovh/standard"
MAX_PIECES      = 5       # activer seulement si ≤ N pièces
TIMEOUT         = 2.0     # timeout HTTP en secondes
CACHE_SIZE      = 2000    # entrées en mémoire
_ENABLED        = True    # désactivé si l'API échoue trop

# ─────────────────────────────────────────────────────────────
#  Cache mémoire
# ─────────────────────────────────────────────────────────────
_cache      = {}
_cache_lock = threading.Lock()
_fail_count = 0   # nombre d'échecs consécutifs
_MAX_FAILS  = 5   # après N échecs → désactiver

def _cache_get(fen_key: str):
    with _cache_lock:
        return _cache.get(fen_key)

def _cache_set(fen_key: str, value):
    with _cache_lock:
        if len(_cache) >= CACHE_SIZE:
            # Supprimer la moitié du cache (simple LRU)
            keys = list(_cache.keys())
            for k in keys[:CACHE_SIZE // 2]:
                del _cache[k]
        _cache[fen_key] = value


# ─────────────────────────────────────────────────────────────
#  Appel API
# ─────────────────────────────────────────────────────────────
def _query_api(board: chess.Board) -> dict | None:
    """
    Interroge l'API Syzygy Lichess.
    Retourne le JSON de réponse ou None si erreur.
    """
    global _fail_count, _ENABLED

    if not _HAS_REQUESTS or not _ENABLED:
        return None

    # Clé de cache : FEN sans compteurs de coups
    parts    = board.fen().split()
    fen_key  = " ".join(parts[:4])
    cached   = _cache_get(fen_key)
    if cached is not None:
        return cached

    try:
        fen_encoded = board.fen().replace(" ", "_")
        url = f"{SYZYGY_URL}?fen={fen_encoded}"
        r   = _requests.get(url, timeout=TIMEOUT)

        if r.status_code != 200:
            _fail_count += 1
            if _fail_count >= _MAX_FAILS:
                print(f"[Syzygy] Trop d'erreurs → désactivé")
                _ENABLED = False
            return None

        data = r.json()
        _cache_set(fen_key, data)
        _fail_count = 0   # reset
        return data

    except Exception:
        _fail_count += 1
        if _fail_count >= _MAX_FAILS:
            print(f"[Syzygy] API non disponible → désactivé")
            _ENABLED = False
        return None


# ─────────────────────────────────────────────────────────────
#  Catégorie WDL (Win/Draw/Loss)
# ─────────────────────────────────────────────────────────────
_WDL_PRIORITY = {
    # Pour le joueur qui joue : meilleur résultat en premier
    "win":           0,
    "cursed-win":    1,
    "draw":          2,
    "blessed-loss":  3,
    "loss":          4,
    "unknown":       5,
}

def _move_priority(move_data: dict) -> int:
    """Priorité d'un coup : 0 = meilleur."""
    cat = move_data.get("category", "unknown")
    dtz = move_data.get("dtz", 9999)
    if dtz is None:
        dtz = 9999
    # Win avec DTZ le plus bas = mat le plus rapide
    return (_WDL_PRIORITY.get(cat, 5), abs(dtz))


# ─────────────────────────────────────────────────────────────
#  Interface publique
# ─────────────────────────────────────────────────────────────
def should_use_syzygy(board: chess.Board) -> bool:
    """
    Retourne True si on doit interroger Syzygy pour cette position.
    Conditions :
      - ≤ MAX_PIECES pièces sur l'échiquier
      - Pas en partie terminée
      - API activée
    """
    if not _ENABLED or not _HAS_REQUESTS:
        return False
    if board.is_game_over():
        return False
    return chess.popcount(board.occupied) <= MAX_PIECES


def get_syzygy_move(board: chess.Board) -> chess.Move | None:
    """
    Retourne le meilleur coup selon les tablebases Syzygy.
    Retourne None si :
      - Position hors tablebase (> MAX_PIECES pièces)
      - API non disponible
      - Erreur réseau
    """
    if not should_use_syzygy(board):
        return None

    data = _query_api(board)
    if not data:
        return None

    moves_data = data.get("moves", [])
    if not moves_data:
        return None

    # Trier les coups par priorité (win DTZ min > draw > loss DTZ max)
    legal = board.legal_moves
    best_move = None
    best_prio = (999, 999)

    for md in moves_data:
        uci_str = md.get("uci", "")
        try:
            mv = chess.Move.from_uci(uci_str)
        except Exception:
            continue
        if mv not in legal:
            continue
        prio = _move_priority(md)
        if prio < best_prio:
            best_prio = prio
            best_move = mv

    return best_move


def get_syzygy_wdl(board: chess.Board) -> str | None:
    """
    Retourne le résultat WDL de la position courante.
    'win', 'draw', 'loss', 'cursed-win', 'blessed-loss' ou None.
    """
    if not should_use_syzygy(board):
        return None
    data = _query_api(board)
    if not data:
        return None
    return data.get("category")


def get_syzygy_stats() -> dict:
    """Stats pour le dashboard."""
    return {
        "enabled":    _ENABLED,
        "cache_size": len(_cache),
        "fail_count": _fail_count,
        "max_pieces": MAX_PIECES,
    }