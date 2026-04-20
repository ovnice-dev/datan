# =============================================================
#  NovaBot – Analyse contextuelle de la position
#  Détecte : phase de jeu, type de position, structure de pions
# =============================================================
"""
Ce module calcule UNE SEULE FOIS par nœud un objet PositionInfo
que l'évaluateur utilise en entier.  Il est conçu pour être
compatible avec une intégration NNUE future (le PositionInfo
sera passé en paramètre au lieu d'un appel réseau indépendant).
"""
import chess

# ─────────────────────────────────────────────────────────────
#  Constantes de phase
# ─────────────────────────────────────────────────────────────
# Poids de matériel pour le calcul de la phase de jeu
_PHASE_WEIGHTS = {
    chess.PAWN:   0,
    chess.KNIGHT: 1,
    chess.BISHOP: 1,
    chess.ROOK:   2,
    chess.QUEEN:  4,
    chess.KING:   0,
}
_MAX_PHASE = 24   # 4 cavaliers/fous + 4 tours + 2 dames

# Phase de jeu
PHASE_OPENING    = 0
PHASE_MIDDLEGAME = 1
PHASE_ENDGAME    = 2

# Type de position
POS_OPEN      = 0   # peu de pions bloqués, fous actifs
POS_SEMI_OPEN = 1
POS_CLOSED    = 2   # beaucoup de pions bloqués, cavaliers préférés

# ─────────────────────────────────────────────────────────────
#  Masques utilitaires précalculés
# ─────────────────────────────────────────────────────────────
# Fichiers (colonnes)
_FILE_MASKS = [chess.BB_FILES[f] for f in range(8)]

# Rangs
_RANK_MASKS = [chess.BB_RANKS[r] for r in range(8)]

# Pions voisins (fichiers adjacents)
_ADJACENT_FILES = {}
for f in range(8):
    mask = 0
    if f > 0: mask |= _FILE_MASKS[f - 1]
    if f < 7: mask |= _FILE_MASKS[f + 1]
    _ADJACENT_FILES[f] = mask

# Zone avant d'un pion (rangs devant lui)
def _front_spans(sq: int, color: chess.Color) -> int:
    """Retourne le masque des cases devant un pion."""
    file_mask = _FILE_MASKS[chess.square_file(sq)]
    rank      = chess.square_rank(sq)
    result = 0
    if color == chess.WHITE:
        for r in range(rank + 1, 8):
            result |= _RANK_MASKS[r]
    else:
        for r in range(rank - 1, -1, -1):
            result |= _RANK_MASKS[r]
    return file_mask & result


# ─────────────────────────────────────────────────────────────
#  Classe principale
# ─────────────────────────────────────────────────────────────
class PositionInfo:
    """
    Analyse complète d'une position échiquéenne.
    Calculée une fois, consultée partout dans l'évaluateur.
    """
    __slots__ = (
        # Phase
        "phase_raw", "phase_factor",   # 0.0 = ouverture, 1.0 = finale
        "game_phase",                  # PHASE_OPENING / MIDDLEGAME / ENDGAME
        # Type de position
        "pos_type",                    # POS_OPEN / SEMI_OPEN / CLOSED
        "open_files",                  # set des colonnes sans pions
        "semi_open_w", "semi_open_b",  # colonnes semi-ouvertes pour chaque camp
        "blocked_pawns",               # nombre de pions bloqués
        # Matériel
        "material_w", "material_b",    # valeur matérielle totale sans pions
        "has_queen_w", "has_queen_b",
        "bishop_count_w", "bishop_count_b",
        "knight_count_w", "knight_count_b",
        # Pions
        "pawns_w", "pawns_b",          # bitboards des pions
        "doubled_w", "doubled_b",      # nombre de pions doublés
        "isolated_w", "isolated_b",    # pions isolés
        "passed_w", "passed_b",        # pions passés
        "pawn_shield_w", "pawn_shield_b",  # score bouclier du roi
        # Roi
        "king_sq_w", "king_sq_b",
        "king_file_w", "king_file_b",
        # Mobilité brute (nb de coups légaux ne sera pas recalculé ici
        # pour éviter le coût – on utilise pseudo-légal)
        "mobility_w", "mobility_b",
    )

    def __init__(self, board: chess.Board):
        self._compute(board)

    def _compute(self, board: chess.Board):
        # ── Phase de jeu ──────────────────────────────────────
        phase = 0
        for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
            count = len(board.pieces(pt, chess.WHITE)) + len(board.pieces(pt, chess.BLACK))
            phase += count * _PHASE_WEIGHTS[pt]
        phase = min(phase, _MAX_PHASE)
        self.phase_raw    = phase
        self.phase_factor = 1.0 - phase / _MAX_PHASE   # 0 = plein milieu, 1 = finale

        if phase >= 20:
            self.game_phase = PHASE_OPENING
        elif phase >= 10:
            self.game_phase = PHASE_MIDDLEGAME
        else:
            self.game_phase = PHASE_ENDGAME

        # ── Pions ─────────────────────────────────────────────
        pw = board.pieces(chess.PAWN, chess.WHITE)
        pb = board.pieces(chess.PAWN, chess.BLACK)
        self.pawns_w = int(pw)
        self.pawns_b = int(pb)

        # Colonnes
        self.open_files   = set()
        self.semi_open_w  = set()
        self.semi_open_b  = set()
        for f in range(8):
            has_w = bool(pw & _FILE_MASKS[f])
            has_b = bool(pb & _FILE_MASKS[f])
            if not has_w and not has_b:
                self.open_files.add(f)
            elif not has_w:
                self.semi_open_w.add(f)
            elif not has_b:
                self.semi_open_b.add(f)

        # Pions bloqués
        blocked = 0
        for sq in pw:
            forward = sq + 8
            if forward < 64 and board.piece_type_at(forward) is not None:
                blocked += 1
        for sq in pb:
            forward = sq - 8
            if forward >= 0 and board.piece_type_at(forward) is not None:
                blocked += 1
        self.blocked_pawns = blocked

        # Type de position (ouvert / semi / fermé)
        ratio = blocked / max(1, len(pw) + len(pb))
        if ratio < 0.20:
            self.pos_type = POS_OPEN
        elif ratio < 0.45:
            self.pos_type = POS_SEMI_OPEN
        else:
            self.pos_type = POS_CLOSED

        # Pions doublés
        self.doubled_w = self._count_doubled(pw)
        self.doubled_b = self._count_doubled(pb)

        # Pions isolés
        self.isolated_w = self._count_isolated(pw)
        self.isolated_b = self._count_isolated(pb)

        # Pions passés
        self.passed_w = self._count_passed(pw, pb, chess.WHITE)
        self.passed_b = self._count_passed(pb, pw, chess.BLACK)

        # ── Matériel ──────────────────────────────────────────
        piece_vals = {chess.KNIGHT: 320, chess.BISHOP: 330,
                      chess.ROOK: 500, chess.QUEEN: 900}
        mat_w = sum(len(board.pieces(pt, chess.WHITE)) * v
                    for pt, v in piece_vals.items())
        mat_b = sum(len(board.pieces(pt, chess.BLACK)) * v
                    for pt, v in piece_vals.items())
        self.material_w = mat_w
        self.material_b = mat_b

        self.has_queen_w = bool(board.pieces(chess.QUEEN,  chess.WHITE))
        self.has_queen_b = bool(board.pieces(chess.QUEEN,  chess.BLACK))
        self.bishop_count_w = len(board.pieces(chess.BISHOP, chess.WHITE))
        self.bishop_count_b = len(board.pieces(chess.BISHOP, chess.BLACK))
        self.knight_count_w = len(board.pieces(chess.KNIGHT, chess.WHITE))
        self.knight_count_b = len(board.pieces(chess.KNIGHT, chess.BLACK))

        # ── Roi ───────────────────────────────────────────────
        ksw = board.king(chess.WHITE)
        ksb = board.king(chess.BLACK)
        self.king_sq_w   = ksw
        self.king_sq_b   = ksb
        self.king_file_w = chess.square_file(ksw) if ksw is not None else 4
        self.king_file_b = chess.square_file(ksb) if ksb is not None else 4

        # Bouclier du roi (pions devant le roi)
        self.pawn_shield_w = self._king_pawn_shield(board, chess.WHITE, ksw, pw)
        self.pawn_shield_b = self._king_pawn_shield(board, chess.BLACK, ksb, pb)

        # ── Mobilité ─────────────────────────────────────────
        # Pseudo-légal pour la vitesse (pas de légal complet ici)
        mob_w = mob_b = 0
        for move in board.pseudo_legal_moves:
            if board.color_at(move.from_square) == chess.WHITE:
                mob_w += 1
            else:
                mob_b += 1
        self.mobility_w = mob_w
        self.mobility_b = mob_b

    # ── Helpers privés ────────────────────────────────────────
    @staticmethod
    def _count_doubled(pawns) -> int:
        count = 0
        for f in range(8):
            n = bin(int(pawns) & int(_FILE_MASKS[f])).count('1')
            if n > 1:
                count += n - 1
        return count

    @staticmethod
    def _count_isolated(pawns) -> int:
        count = 0
        pawn_int = int(pawns)
        for sq in pawns:
            f = chess.square_file(sq)
            if not (pawn_int & _ADJACENT_FILES[f]):
                count += 1
        return count

    @staticmethod
    def _count_passed(my_pawns, opp_pawns, color: chess.Color) -> int:
        """Compte les pions passés : aucun pion adverse sur la colonne ou colonnes adjacentes devant."""
        count = 0
        opp_int = int(opp_pawns)
        for sq in my_pawns:
            f = chess.square_file(sq)
            # Masque = colonne propre + adjacentes, cases devant le pion
            front = _front_spans(sq, color)
            block_mask = front & (_FILE_MASKS[f] | _ADJACENT_FILES[f])
            if not (opp_int & block_mask):
                count += 1
        return count

    @staticmethod
    def _king_pawn_shield(board: chess.Board, color: chess.Color,
                          king_sq, pawns) -> int:
        """Score du bouclier de pions autour du roi (0–6)."""
        if king_sq is None:
            return 0
        kf = chess.square_file(king_sq)
        kr = chess.square_rank(king_sq)
        shield = 0
        direction = 1 if color == chess.WHITE else -1
        for df in (-1, 0, 1):
            f2 = kf + df
            if not (0 <= f2 <= 7):
                continue
            for dr in (1, 2):
                r2 = kr + direction * dr
                if not (0 <= r2 <= 7):
                    continue
                sq2 = chess.square(f2, r2)
                if board.piece_type_at(sq2) == chess.PAWN and board.color_at(sq2) == color:
                    shield += 1 if dr == 1 else 0   # rang immédiat = +1, rang 2 = rien
        return shield
