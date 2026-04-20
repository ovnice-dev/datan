# =============================================================
#  NovaBot – Livre d'ouvertures intégré  (Étape 5)
#
#  Deux niveaux :
#    1. Fichier .bin Polyglot externe (si présent)
#    2. Livre hardcodé avec les lignes principales
#       (fallback garanti, aucune dépendance)
#
#  Usage :
#    from engine.opening_book import get_book_move
#    move = get_book_move(board)   # None si hors du livre
# =============================================================
import os
import random
import chess
import chess.polyglot

# ─────────────────────────────────────────────────────────────
#  Livre Polyglot externe
# ─────────────────────────────────────────────────────────────
#  Livres Polyglot multiples (cascade)
# ─────────────────────────────────────────────────────────────
_poly_readers = []   # liste de (path, reader) dans l'ordre de priorité
_poly_path    = None  # gardé pour compatibilité get_book_stats()

def load_polyglot(path: str) -> bool:
    """
    Charge un ou plusieurs fichiers .bin Polyglot.
    Accepte :
      - une string  : "Titans.bin"
      - une liste   : ["Titans.bin", "komodo.bin", "rodent.bin"]
    Les livres sont consultés dans l'ordre — si le premier ne trouve
    pas de coup, on passe au suivant, etc.
    """
    global _poly_readers, _poly_path

    # Normaliser en liste
    if isinstance(path, str):
        paths = [p.strip() for p in path.split(",") if p.strip()]
    else:
        paths = list(path)

    _poly_readers = []
    for p in paths:
        if not os.path.exists(p):
            print(f"[Book] ⚠ Fichier introuvable : {p}")
            continue
        try:
            reader = chess.polyglot.open_reader(p)
            _poly_readers.append((p, reader))
            print(f"[Book] ✓ Livre chargé : {p}")
        except Exception as e:
            print(f"[Book] ✗ Erreur chargement {p} : {e}")

    if _poly_readers:
        _poly_path = ", ".join(str(p) for p, _ in _poly_readers)

        return True
    return False


def _get_polyglot_move(board: chess.Board) -> chess.Move | None:
    """
    Consulte les livres dans l'ordre.
    Dès qu'un livre trouve un coup → on le retourne.
    Si aucun livre ne trouve → None.
    """
    for path, reader in _poly_readers:
        try:
            entries = list(reader.find_all(board))
            if not entries:
                continue
            # Sélection pondérée
            total = sum(e.weight for e in entries)
            if total == 0:
                mv = entries[0].move
            else:
                r     = random.randint(0, total - 1)
                cumul = 0
                mv    = entries[0].move
                for e in entries:
                    cumul += e.weight
                    if r < cumul:
                        mv = e.move
                        break
            if mv in board.legal_moves:
                return mv
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────
#  Livre hardcodé (FEN → list[UCI])
#  Couvre les ouvertures principales jusqu'au coup 15 environ
# ─────────────────────────────────────────────────────────────
# Format : clé = Zobrist-style sur FEN, valeur = liste de coups UCI avec poids
# On utilise le FEN complet pour éviter les collisions

_BOOK: dict[str, list[tuple[str, int]]] = {}

def _add(moves_uci: list[str], line_moves: list[tuple[str, int]]):
    """Ajoute les entrées du livre pour chaque position de la ligne."""
    board = chess.Board()
    for uci in moves_uci:
        try:
            board.push(chess.Move.from_uci(uci))
        except Exception:
            break
    fen_key = _fen_key(board)
    existing = _BOOK.get(fen_key, [])
    for mv_uci, weight in line_moves:
        # Éviter les doublons
        if not any(m == mv_uci for m, _ in existing):
            existing.append((mv_uci, weight))
    _BOOK[fen_key] = existing

def _fen_key(board: chess.Board) -> str:
    """Clé unique : pièces + trait + roques + en passant (sans compteurs)."""
    parts = board.fen().split()
    return " ".join(parts[:4])

def _build_book():
    """Construit le livre hardcodé."""

    # ── Position de départ ──────────────────────────────────
    _add([], [
        ("e2e4", 100), ("d2d4", 90), ("g1f3", 70),
        ("c2c4", 60),  ("b1c3", 30),
    ])

    # ── 1.e4 ────────────────────────────────────────────────
    _add(["e2e4"], [
        ("e7e5", 90), ("c7c5", 85), ("e7e6", 70),
        ("c7c6", 60), ("d7d5", 50), ("g8f6", 40),
    ])

    # 1.e4 e5 – Ouvertures ouvertes
    _add(["e2e4","e7e5"], [
        ("g1f3", 95), ("f2f4", 30), ("b1c3", 40), ("d2d4", 20),
    ])
    _add(["e2e4","e7e5","g1f3"], [
        ("b8c6", 90), ("g8f6", 70), ("d7d6", 30), ("f7f5", 20),
    ])

    # Ruy Lopez
    _add(["e2e4","e7e5","g1f3","b8c6"], [
        ("f1b5", 90), ("f1c4", 80), ("d2d4", 50), ("b1c3", 60),
    ])
    _add(["e2e4","e7e5","g1f3","b8c6","f1b5"], [
        ("a7a6", 90), ("g8f6", 70), ("f8c5", 50), ("b7b5", 30),
    ])
    _add(["e2e4","e7e5","g1f3","b8c6","f1b5","a7a6"], [
        ("b5a4", 90), ("b5c6", 70),
    ])
    _add(["e2e4","e7e5","g1f3","b8c6","f1b5","a7a6","b5a4"], [
        ("g8f6", 90), ("d7d6", 50), ("f7f5", 30),
    ])
    _add(["e2e4","e7e5","g1f3","b8c6","f1b5","a7a6","b5a4","g8f6"], [
        ("e1g1", 90), ("d2d3", 40), ("b1c3", 40),
    ])
    _add(["e2e4","e7e5","g1f3","b8c6","f1b5","a7a6","b5a4","g8f6","e1g1"], [
        ("f8e7", 80), ("b7b5", 70), ("d7d6", 50),
    ])

    # Italian Game
    _add(["e2e4","e7e5","g1f3","b8c6","f1c4"], [
        ("f8c5", 80), ("g8f6", 70), ("f8e7", 50), ("d7d6", 40),
    ])
    _add(["e2e4","e7e5","g1f3","b8c6","f1c4","f8c5"], [
        ("c2c3", 80), ("b2b4", 50), ("d2d3", 60), ("e1g1", 50),
    ])
    _add(["e2e4","e7e5","g1f3","b8c6","f1c4","f8c5","c2c3"], [
        ("g8f6", 80), ("d7d6", 60), ("e8g8", 40),
    ])

    # Petrov
    _add(["e2e4","e7e5","g1f3","g8f6"], [
        ("f3e5", 80), ("b1c3", 60), ("f1c4", 40),
    ])

    # ── 1.e4 c5 – Sicilienne ────────────────────────────────
    _add(["e2e4","c7c5"], [
        ("g1f3", 95), ("b1c3", 50), ("c2c3", 40), ("f2f4", 30),
    ])
    _add(["e2e4","c7c5","g1f3"], [
        ("d7d6", 80), ("b8c6", 75), ("e7e6", 70), ("g8f6", 40),
    ])
    _add(["e2e4","c7c5","g1f3","d7d6"], [
        ("d2d4", 90),
    ])
    _add(["e2e4","c7c5","g1f3","d7d6","d2d4"], [
        ("c5d4", 95),
    ])
    _add(["e2e4","c7c5","g1f3","d7d6","d2d4","c5d4"], [
        ("f3d4", 95),
    ])
    _add(["e2e4","c7c5","g1f3","d7d6","d2d4","c5d4","f3d4"], [
        ("g8f6", 80), ("b8c6", 70), ("a7a6", 60),
    ])
    # Najdorf
    _add(["e2e4","c7c5","g1f3","d7d6","d2d4","c5d4","f3d4","g8f6"], [
        ("b1c3", 90),
    ])
    _add(["e2e4","c7c5","g1f3","d7d6","d2d4","c5d4","f3d4","g8f6","b1c3"], [
        ("a7a6", 80), ("e7e6", 60), ("b8c6", 50),
    ])
    # Sicilienne Dragon
    _add(["e2e4","c7c5","g1f3","b8c6","d2d4","c5d4","f3d4","g7g6"], [
        ("b1c3", 90), ("c1e3", 70),
    ])

    # ── 1.e4 e6 – Française ─────────────────────────────────
    _add(["e2e4","e7e6"], [
        ("d2d4", 90), ("d2d3", 30),
    ])
    _add(["e2e4","e7e6","d2d4"], [
        ("d7d5", 95),
    ])
    _add(["e2e4","e7e6","d2d4","d7d5"], [
        ("b1c3", 80), ("b1d2", 70), ("e4e5", 60), ("e4d5", 30),
    ])
    _add(["e2e4","e7e6","d2d4","d7d5","b1c3"], [
        ("f8b4", 80), ("g8f6", 70), ("d5e4", 40),
    ])

    # ── 1.e4 c6 – Caro-Kann ─────────────────────────────────
    _add(["e2e4","c7c6"], [
        ("d2d4", 90), ("b1c3", 40),
    ])
    _add(["e2e4","c7c6","d2d4"], [
        ("d7d5", 95),
    ])
    _add(["e2e4","c7c6","d2d4","d7d5"], [
        ("b1c3", 80), ("e4e5", 60), ("e4d5", 50), ("b1d2", 70),
    ])
    _add(["e2e4","c7c6","d2d4","d7d5","b1c3"], [
        ("d5e4", 80), ("g8f6", 60), ("e7e6", 50),
    ])

    # ── 1.d4 ────────────────────────────────────────────────
    _add(["d2d4"], [
        ("d7d5", 85), ("g8f6", 80), ("f7f5", 30), ("e7e6", 50),
    ])
    _add(["d2d4","d7d5"], [
        ("c2c4", 90), ("g1f3", 60), ("b1c3", 40),
    ])
    _add(["d2d4","d7d5","c2c4"], [
        ("e7e6", 85), ("c7c6", 80), ("d5c4", 60), ("g8f6", 70),
    ])

    # Dame Gambit Accepté
    _add(["d2d4","d7d5","c2c4","d5c4"], [
        ("g1f3", 90), ("e2e3", 70), ("e2e4", 60),
    ])
    # Dame Gambit Refusé
    _add(["d2d4","d7d5","c2c4","e7e6"], [
        ("b1c3", 90), ("g1f3", 80), ("c4d5", 40),
    ])
    _add(["d2d4","d7d5","c2c4","e7e6","b1c3"], [
        ("g8f6", 90), ("c7c5", 60), ("f8e7", 50),
    ])
    _add(["d2d4","d7d5","c2c4","e7e6","b1c3","g8f6"], [
        ("c1g5", 80), ("g1f3", 80), ("e2e3", 60),
    ])

    # Nimzo-Indien
    _add(["d2d4","g8f6"], [
        ("c2c4", 90), ("g1f3", 70),
    ])
    _add(["d2d4","g8f6","c2c4"], [
        ("e7e6", 80), ("g7g6", 70), ("c7c5", 60), ("d7d6", 50),
    ])
    _add(["d2d4","g8f6","c2c4","e7e6"], [
        ("b1c3", 90), ("g1f3", 60),
    ])
    _add(["d2d4","g8f6","c2c4","e7e6","b1c3"], [
        ("f8b4", 80), ("d7d5", 70), ("f8e7", 50),
    ])

    # Défense Grunfeld
    _add(["d2d4","g8f6","c2c4","g7g6"], [
        ("b1c3", 90), ("g1f3", 70), ("g2g3", 60),
    ])
    _add(["d2d4","g8f6","c2c4","g7g6","b1c3"], [
        ("d7d5", 80), ("f8g7", 70), ("e7e6", 40),
    ])
    _add(["d2d4","g8f6","c2c4","g7g6","b1c3","d7d5"], [
        ("c4d5", 90), ("g1f3", 60), ("e2e4", 80),
    ])

    # ── 1.c4 – Anglaise ─────────────────────────────────────
    _add(["c2c4"], [
        ("e7e5", 80), ("c7c5", 70), ("g8f6", 75), ("e7e6", 60),
    ])
    _add(["c2c4","e7e5"], [
        ("b1c3", 90), ("g1f3", 70),
    ])

    # ── 1.Nf3 ───────────────────────────────────────────────
    _add(["g1f3"], [
        ("d7d5", 80), ("g8f6", 75), ("c7c5", 70), ("e7e6", 60),
    ])
    _add(["g1f3","d7d5"], [
        ("d2d4", 80), ("c2c4", 70), ("g2g3", 50),
    ])

    # ── Fin de roques rapides ────────────────────────────────
    # Si le roi n'a pas encore roqué dans les positions communes
    for line in [
        ["e2e4","e7e5","g1f3","b8c6","f1c4","f8c5","c2c3","g8f6","d2d4"],
        ["d2d4","d7d5","c2c4","e7e6","b1c3","g8f6","g1f3"],
    ]:
        _add(line, [("e1g1", 80)])

_build_book()

# ─────────────────────────────────────────────────────────────
#  Interface publique
# ─────────────────────────────────────────────────────────────
def get_book_move(board: chess.Board,
                  book_path: str | None = None) -> chess.Move | None:
    """
    Retourne un coup du livre d'ouvertures, ou None.

    Priorité :
      1. Titans.bin  → le plus complet
      2. komodo.bin  → si pas trouvé dans Titans
      3. rodent.bin  → si pas trouvé dans komodo
      4. gm2001.bin  → dernier recours Polyglot
      5. Livre hardcodé → fallback final garanti
    """
    global _poly_readers

    # Charger les livres si pas encore fait
    if not _poly_readers and book_path:
        load_polyglot(book_path)

    # 1. Polyglot cascade
    mv = _get_polyglot_move(board)
    if mv is not None and mv in board.legal_moves:
        return mv

    # 2. Livre hardcodé (fallback)
    key     = _fen_key(board)
    entries = _BOOK.get(key, [])
    if not entries:
        return None

    legal_entries = []
    for uci, weight in entries:
        try:
            mv = chess.Move.from_uci(uci)
            if mv in board.legal_moves:
                legal_entries.append((mv, weight))
        except Exception:
            continue

    if not legal_entries:
        return None

    total = sum(w for _, w in legal_entries)
    if total == 0:
        return legal_entries[0][0]
    r = random.randint(0, total - 1)
    cumul = 0
    for mv, w in legal_entries:
        cumul += w
        if r < cumul:
            return mv
    return legal_entries[0][0]

    # 1. Polyglot
    mv = _get_polyglot_move(board)
    if mv is not None and mv in board.legal_moves:
        return mv

    # 2. Livre hardcodé
    key     = _fen_key(board)
    entries = _BOOK.get(key, [])
    if not entries:
        return None

    # Filtrer les coups illégaux
    legal_entries = []
    for uci, weight in entries:
        try:
            mv = chess.Move.from_uci(uci)
            if mv in board.legal_moves:
                legal_entries.append((mv, weight))
        except Exception:
            continue

    if not legal_entries:
        return None

    # Sélection pondérée
    total = sum(w for _, w in legal_entries)
    if total == 0:
        return legal_entries[0][0]
    r = random.randint(0, total - 1)
    cumul = 0
    for mv, w in legal_entries:
        cumul += w
        if r < cumul:
            return mv
    return legal_entries[0][0]


def get_book_stats() -> dict:
    """Statistiques du livre."""
    return {
        "positions":  len(_BOOK),
        "polyglot":   _poly_path or "Non chargé",
        "total_moves": sum(len(v) for v in _BOOK.values()),
    }