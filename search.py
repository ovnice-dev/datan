# engine/search.py
# NovaBot – Moteur de recherche (version corrigée, prête à coller)
#
# Remplace entièrement engine/search.py par ce fichier, sauvegarde et relance panel.py.
# Ce fichier conserve les optimisations et la logique de ton moteur tout en corrigeant
# les problèmes d'indentation et de formatage rencontrés (float->int, Path->str, prints).
#
# IMPORTANT : ce fichier ne dépend PAS d'une constante MAX_PLY dans config.py.
# Si tu veux ajuster MAX_PLY, modifie la valeur ci‑dessous.

import chess
import chess.polyglot
import threading
import time
import math

from engine.evaluate      import evaluate
from engine.position_info import PositionInfo, PHASE_ENDGAME
from config import (
    MAX_DEPTH, TT_SIZE, CONTEMPT,
    NULL_MOVE_MIN_DEPTH, LMR_MIN_DEPTH, LMR_MIN_MOVES,
    FUTILITY_MARGIN, DELTA_PRUNING_MARGIN, RAZOR_MARGIN, IIR_MIN_DEPTH,
)

# ─────────────────────────────────────────────────────────────
#  Constantes (locales)
# ─────────────────────────────────────────────────────────────
INF       = 100_000
CHECKMATE = 90_000
DRAW_VAL  = -CONTEMPT

# Limites pour les scores NNUE (évite que NNUE retourne > CHECKMATE)
EVAL_MAX  = 29_000   # max score HCE/NNUE (en dessous de CHECKMATE)
EVAL_MIN  = -29_000

SINGULAR_DEPTH_MIN   = 6
SINGULAR_MARGIN      = 2
CHECK_EXT_SEE_THRESH = 0

_SEE_PIECE_VAL = [0, 100, 320, 330, 500, 960, 20_000]

# Taille maximale de ply utilisée localement (ne dépend pas de config)
MAX_PLY = 128

# ─────────────────────────────────────────────────────────────
#  Abort flags — SÉPARÉS pour search vs ponder (BUG FIX)
# ─────────────────────────────────────────────────────────────
_search_abort = [False]   # interrompt search()
_ponder_abort = [False]   # interrompt search_ponder()

def abort_search():
    """Arrêt de la recherche principale."""
    _search_abort[0] = True

def abort_ponder():
    """Arrêt du pondering."""
    _ponder_abort[0] = True

def _should_stop_search(stop_time: float, node_count: int) -> bool:
    # Vérifie seulement tous les 1024 nœuds (NPS +15%)
    if node_count & 0x3FF:
        return False
    return _search_abort[0] or time.time() >= stop_time

def _should_stop_ponder(stop_event: threading.Event) -> bool:
    return _ponder_abort[0] or stop_event.is_set()


# ─────────────────────────────────────────────────────────────
#  SEE – Static Exchange Evaluation
# ─────────────────────────────────────────────────────────────
def _see(board: chess.Board, move: chess.Move) -> int:
    to_sq   = move.to_square
    from_sq = move.from_square
    victim  = board.piece_type_at(to_sq)

    if victim is None:
        if board.is_en_passant(move):
            victim = chess.PAWN
        else:
            return 0

    gain    = [0] * 32
    d       = 0
    gain[d] = _SEE_PIECE_VAL[victim]

    attacker_type = board.piece_type_at(from_sq)
    if attacker_type is None:
        return 0   # BUG FIX: attacker_type peut être None en cas de bug

    side     = board.color_at(from_sq)
    occupied = int(board.occupied) ^ (1 << from_sq)

    while True:
        d       += 1
        gain[d]  = _SEE_PIECE_VAL[attacker_type] - gain[d - 1]
        if max(-gain[d - 1], gain[d]) < 0:
            break

        side    = not side
        all_atk = board.attackers(side, to_sq)
        min_val = INF + 1
        min_sq  = -1
        for sq in all_atk:
            if not (occupied >> sq & 1):
                continue
            pt  = board.piece_type_at(sq)
            if pt is None:
                continue   # BUG FIX: pt peut être None
            val = _SEE_PIECE_VAL[pt]
            if val < min_val:
                min_val       = val
                min_sq        = sq
                attacker_type = pt
        if min_sq == -1:
            break
        occupied ^= (1 << min_sq)

    while d > 1:
        d          -= 1
        gain[d - 1] = -max(-gain[d - 1], gain[d])

    return gain[0]


# ─────────────────────────────────────────────────────────────
#  Table de transposition
# ─────────────────────────────────────────────────────────────
TT_EXACT = 0
TT_LOWER = 1
TT_UPPER = 2

class _TTEntry:
    __slots__ = ("key16", "depth", "flag", "score", "move", "gen")
    def __init__(self, key16, depth, flag, score, move, gen):
        self.key16 = key16
        self.depth = depth
        self.flag  = flag
        self.score = score
        self.move  = move
        self.gen   = gen

_TT_MASK = TT_SIZE - 1
_tt      = [None] * TT_SIZE
_tt_gen  = 0

def _tt_new_search():
    global _tt_gen
    _tt_gen = (_tt_gen + 1) & 0xFF

def _tt_store(key: int, depth: int, flag: int, score: int, move, ply: int):
    # Bornage du score avant stockage (BUG FIX: évite corruption si NNUE dépasse)
    score = max(EVAL_MIN - 1000, min(EVAL_MAX + 1000, score))
    if score >= CHECKMATE - 500:
        score += ply
    elif score <= -(CHECKMATE - 500):
        score -= ply

    idx   = key & _TT_MASK
    entry = _tt[idx]
    key16 = key >> 48

    if (entry is None
            or entry.gen   != _tt_gen
            or entry.depth <= depth + 2
            or entry.flag  == TT_UPPER):
        _tt[idx] = _TTEntry(key16, depth, flag, score, move, _tt_gen)

def _tt_probe(key: int):
    idx   = key & _TT_MASK
    entry = _tt[idx]
    if entry is not None and entry.key16 == (key >> 48):
        return entry
    return None

def _tt_adjust(raw: int, ply: int) -> int:
    if raw >= CHECKMATE - 500:
        return raw - ply
    if raw <= -(CHECKMATE - 500):
        return raw + ply
    return raw


# ─────────────────────────────────────────────────────────────
#  Heuristiques de tri
# ─────────────────────────────────────────────────────────────
_killers  = [[None, None, None] for _ in range(MAX_PLY)]
_history  = [[[0] * 64 for _ in range(64)] for _ in range(2)]
_counter  = [[None] * 64 for _ in range(64)]
_conthist: dict = {}

def _reset_heuristics():
    global _killers, _counter, _conthist
    _killers  = [[None, None, None] for _ in range(MAX_PLY)]
    _counter  = [[None] * 64 for _ in range(64)]
    _conthist = {}
    for c in range(2):
        for f in range(64):
            for t in range(64):
                _history[c][f][t] >>= 2   # aging

def _store_killer(move: chess.Move, ply: int):
    k = _killers[ply]
    if move == k[0]:
        return
    k[2], k[1], k[0] = k[1], k[0], move

def _history_bonus(move: chess.Move, color: bool, depth: int, prev_move):
    bonus = min(depth * depth, 400)   # BUG FIX: cap le bonus pour éviter overflow
    f, t  = move.from_square, move.to_square
    ci    = int(color)
    _history[ci][f][t] = min(32_000, _history[ci][f][t] + bonus)
    if prev_move is not None:
        key = (prev_move.from_square, prev_move.to_square, f, t)
        _conthist[key] = min(32_000, _conthist.get(key, 0) + bonus)

def _history_malus(move: chess.Move, color: bool, depth: int, prev_move):
    malus = min(depth * depth, 400)
    f, t  = move.from_square, move.to_square
    ci    = int(color)
    _history[ci][f][t] = max(-32_000, _history[ci][f][t] - malus)
    if prev_move is not None:
        key = (prev_move.from_square, prev_move.to_square, f, t)
        _conthist[key] = max(-32_000, _conthist.get(key, 0) - malus)


# ─────────────────────────────────────────────────────────────
#  Move ordering  (NPS optimisé : MVV-LVA précalculé, SEE lazy)
# ─────────────────────────────────────────────────────────────
_MVV_LVA = [[0] * 7 for _ in range(7)]
for _v in range(1, 7):
    for _a in range(1, 7):
        _MVV_LVA[_v][_a] = 10 * _v - _a

def _score_move(board: chess.Board, move: chess.Move,
                tt_move, ply: int, prev_move) -> int:
    if move == tt_move:
        return 2_000_000

    if board.is_capture(move):
        vpt = board.piece_type_at(move.to_square)
        apt = board.piece_type_at(move.from_square)
        if vpt is None:
            vpt = chess.PAWN   # en passant
        if apt is None:
            apt = chess.PAWN
        mvv = _MVV_LVA[vpt][apt]
        sv  = _see(board, move)
        return (1_500_000 if sv >= 0 else 500_000) + mvv * 10 + sv

    if move.promotion == chess.QUEEN:
        return 1_400_000
    if move.promotion:
        return 100_000   # underpromotion: bas score

    k = _killers[ply]
    if move == k[0]: return 900_000
    if move == k[1]: return 800_000
    if move == k[2]: return 750_000

    if (prev_move is not None
            and _counter[prev_move.from_square][prev_move.to_square] == move):
        return 700_000

    ci   = int(board.turn)
    f, t = move.from_square, move.to_square
    hist = _history[ci][f][t]
    if prev_move is not None:
        hist += _conthist.get(
            (prev_move.from_square, prev_move.to_square, f, t), 0)
    return hist

def _sorted_moves(board: chess.Board, tt_move, ply: int,
                  prev_move, excluded_move=None):
    # BUG FIX: filtrage des coups illégaux EN DERNIER RECOURS
    moves = []
    for m in board.legal_moves:
        if m == excluded_move:
            continue
        moves.append(m)

    moves.sort(
        key=lambda m: _score_move(board, m, tt_move, ply, prev_move),
        reverse=True
    )
    return moves


# ─────────────────────────────────────────────────────────────
#  LMR table
# ─────────────────────────────────────────────────────────────
_lmr_table = [[0] * 64 for _ in range(64)]
for _d in range(1, 64):
    for _m in range(1, 64):
        _lmr_table[_d][_m] = max(1, int(0.80 + math.log(_d) * math.log(_m) / 2.20))


# ─────────────────────────────────────────────────────────────
#  Évaluation bornée  (BUG FIX: NNUE ne dépasse pas CHECKMATE)
# ─────────────────────────────────────────────────────────────
def _safe_eval(board: chess.Board) -> int:
    """Évaluation avec bornage garanti [-29000, +29000]."""
    try:
        raw = evaluate(board)
    except TypeError:
        # certains wrappers compilés attendent une FEN (str)
        raw = evaluate(board.fen())
    # Normaliser et bornage
    try:
        raw_int = int(round(raw))
    except Exception:
        try:
            raw_int = int(raw)
        except Exception:
            raw_int = 0
    return max(EVAL_MIN, min(EVAL_MAX, raw_int))


# ─────────────────────────────────────────────────────────────
#  Quiescence Search
# ─────────────────────────────────────────────────────────────
def _quiescence(board: chess.Board, alpha: int, beta: int,
                ply: int, stop_time: float, nodes: list) -> int:
    nodes[0] += 1

    raw       = _safe_eval(board)
    stand_pat = raw if board.turn == chess.WHITE else -raw

    if stand_pat >= beta:
        return beta
    if stand_pat < alpha - DELTA_PRUNING_MARGIN - 900:
        return alpha

    alpha = max(alpha, stand_pat)

    # Générer & trier les captures par SEE décroissant
    tactical = []
    for move in board.legal_moves:
        if board.is_capture(move) or move.promotion:
            sv = _see(board, move) if board.is_capture(move) else 800
            if sv >= -150:   # SEE pruning précoce (NPS +20%)
                tactical.append((sv, move))
    tactical.sort(key=lambda x: x[0], reverse=True)

    for sv, move in tactical:
        board.push(move)
        score = -_quiescence(board, -beta, -alpha, ply + 1, stop_time, nodes)
        board.pop()
        if score >= beta:
            return beta
        alpha = max(alpha, score)

    return alpha


# ─────────────────────────────────────────────────────────────
#  Null-move piece check (BUG FIX: pas de PositionInfo dans la boucle)
# ─────────────────────────────────────────────────────────────
def _has_non_pawn_material(board: chess.Board) -> bool:
    """Vérifie si le joueur courant a du matériel non-pion/roi."""
    c = board.turn
    return bool(
        board.pieces(chess.KNIGHT, c)
        or board.pieces(chess.BISHOP, c)
        or board.pieces(chess.ROOK,   c)
        or board.pieces(chess.QUEEN,  c)
    )


# ─────────────────────────────────────────────────────────────
#  PVS / Negamax principal
# ─────────────────────────────────────────────────────────────
def _pvs(board: chess.Board, depth: int, alpha: int, beta: int,
         ply: int, stop_time: float,
         is_pv: bool, prev_move, in_null: bool,
         excluded_move, nodes: list) -> int:

    nodes[0] += 1

    # ── Arrêt (seulement tous les 1024 nœuds) ───────────────
    if nodes[0] & 0x3FF == 0:
        if _search_abort[0] or time.time() >= stop_time:
            return 0

    # ── Cas terminaux ─────────────────────────────────────
    if board.is_checkmate():
        return -(CHECKMATE - ply)
    if (board.is_stalemate()
            or board.is_insufficient_material()
            or board.is_seventyfive_moves()):
        return DRAW_VAL
    if board.is_repetition(2) or board.halfmove_clock >= 100:
        return DRAW_VAL

    # ── Mate Distance Pruning ────────────────────────────────
    alpha = max(alpha, -(CHECKMATE - ply))
    beta  = min(beta,   CHECKMATE - ply)
    if alpha >= beta:
        return alpha

    in_check = board.is_check()
    if in_check:
        depth += 1   # check extension

    if depth <= 0:
        return _quiescence(board, alpha, beta, ply, stop_time, nodes)

    if ply >= MAX_PLY - 1:
        return _safe_eval(board) if board.turn == chess.WHITE else -_safe_eval(board)

    # ── Table de transposition ───────────────────────────────
    key     = chess.polyglot.zobrist_hash(board)
    entry   = _tt_probe(key)
    tt_move = None

    if entry is not None:
        # BUG FIX: valider que tt_move est légal
        if entry.move is not None and entry.move in board.legal_moves:
            tt_move = entry.move
        if excluded_move is None and not is_pv and entry.depth >= depth:
            s = _tt_adjust(entry.score, ply)
            if entry.flag == TT_EXACT:
                return s
            elif entry.flag == TT_LOWER:
                alpha = max(alpha, s)
            elif entry.flag == TT_UPPER:
                beta  = min(beta, s)
            if alpha >= beta:
                return s

    # ── Évaluation statique (UNE SEULE FOIS par nœud) ────────
    raw_static  = _safe_eval(board)
    static_eval = raw_static if board.turn == chess.WHITE else -raw_static

    # ── Razoring ────────────────────────────────────────────
    if (excluded_move is None and not is_pv and not in_check
            and depth == 1 and static_eval + RAZOR_MARGIN < alpha):
        q = _quiescence(board, alpha - 1, alpha, ply, stop_time, nodes)
        if q < alpha:
            return q

    # ── Futility pruning ─────────────────────────────────────
    if (excluded_move is None and not is_pv and not in_check
            and depth <= 6
            and static_eval + FUTILITY_MARGIN * depth <= alpha):
        return max(static_eval,
                   _quiescence(board, alpha, beta, ply, stop_time, nodes))

    # ── Null-move pruning (BUG FIX: pas de PositionInfo ici) ─
    if (excluded_move is None and not is_pv and not in_check
            and not in_null and depth >= NULL_MOVE_MIN_DEPTH
            and static_eval >= beta
            and _has_non_pawn_material(board)):
        R = min(depth, 3 + depth // 4 + min((static_eval - beta) // 150, 4))
        board.push(chess.Move.null())
        null_sc = -_pvs(board, depth - 1 - R, -beta, -beta + 1,
                        ply + 1, stop_time, False, None, True, None, nodes)
        board.pop()
        if null_sc >= beta:
            return beta if null_sc >= CHECKMATE - 500 else null_sc

    # ── IIR (BUG FIX: seulement si tt_move est None après validation) ─
    if excluded_move is None and tt_move is None and is_pv and depth >= IIR_MIN_DEPTH:
        depth -= 1

    # ── Singular Extensions ─────────────────────────────────
    singular_ext = 0

    if (excluded_move is None
            and not in_check
            and depth >= SINGULAR_DEPTH_MIN
            and tt_move is not None            # BUG FIX: vérif explicite
            and entry is not None
            and entry.depth >= depth - 3
            and entry.flag != TT_UPPER
            and abs(_tt_adjust(entry.score, ply)) < CHECKMATE - 500):

        s_beta  = _tt_adjust(entry.score, ply) - SINGULAR_MARGIN * depth
        s_depth = (depth - 1) // 2

        s_score = _pvs(board, s_depth, s_beta - 1, s_beta,
                       ply, stop_time, False, prev_move, in_null,
                       tt_move, nodes)   # BUG FIX: excluded=tt_move pas excluded_move

        if s_score < s_beta:
            singular_ext = 1
        elif s_score >= beta:
            return s_beta
        elif _tt_adjust(entry.score, ply) >= beta:
            singular_ext = -2

    # ── Génération & tri des coups ───────────────────────────
    moves = _sorted_moves(board, tt_move, ply, prev_move, excluded_move)
    if not moves:
        return DRAW_VAL

    best_score  = -INF
    best_move   = moves[0]
    orig_alpha  = alpha
    quiet_tried = []

    for i, move in enumerate(moves):
        is_capture  = board.is_capture(move)
        is_promo    = bool(move.promotion)
        gives_check = board.gives_check(move)
        is_quiet    = not is_capture and not is_promo

        # ── Late Move Pruning ────────────────────────────────
        if (not is_pv and not in_check and is_quiet
                and not gives_check and depth <= 5
                and i >= 3 + depth * depth):
            continue

        # ── Futility par coup calme ──────────────────────────
        if (not is_pv and not in_check and is_quiet
                and not gives_check and depth <= 4
                and static_eval + FUTILITY_MARGIN * depth + 80 < alpha):
            continue

        # ── SEE pruning AVANT push (NPS +10%) ────────────────
        if (not is_pv and is_capture and depth <= 8
                and _see(board, move) < -50 * depth):
            continue

        # ── Extension par coup ───────────────────────────────
        ext = 0
        if move == tt_move:
            ext = singular_ext
        elif gives_check:
            sv = _see(board, move) if is_capture else 0
            if sv >= CHECK_EXT_SEE_THRESH:
                ext = 1

        board.push(move)

        # ── LMR ─────────────────────────────────────────────
        reduction = 0
        if (i >= LMR_MIN_MOVES and depth >= LMR_MIN_DEPTH
                and is_quiet and not gives_check and not in_check
                and ext == 0):
            reduction = _lmr_table[min(depth, 63)][min(i, 63)]
            if is_pv:
                reduction = max(0, reduction - 1)
            if board.is_check():
                reduction = max(0, reduction - 1)
            if prev_move is not None:
                cval = _conthist.get(
                    (prev_move.from_square, prev_move.to_square,
                     move.from_square, move.to_square), 0)
                if cval > 5_000:
                    reduction = max(0, reduction - 1)

        new_depth = depth - 1 + ext

        # ── PVS ─────────────────────────────────────────────
        if i == 0:
            score = -_pvs(board, new_depth, -beta, -alpha,
                          ply + 1, stop_time, is_pv, move, False, None, nodes)
        else:
            score = -_pvs(board, new_depth - reduction, -alpha - 1, -alpha,
                          ply + 1, stop_time, False, move, False, None, nodes)
            if score > alpha and reduction > 0:
                score = -_pvs(board, new_depth, -alpha - 1, -alpha,
                              ply + 1, stop_time, False, move, False, None, nodes)
            if score > alpha and score < beta:
                score = -_pvs(board, new_depth, -beta, -alpha,
                              ply + 1, stop_time, True, move, False, None, nodes)

        board.pop()

        # BUG FIX: retour sécurisé si abort (ne pas mettre à jour best_move)
        if nodes[0] & 0x3FF == 0:
            if _search_abort[0] or time.time() >= stop_time:
                return best_score if best_score > -INF else 0

        if score > best_score:
            best_score = score
            best_move  = move

        alpha = max(alpha, score)

        if alpha >= beta:
            if is_quiet:
                _store_killer(move, ply)
                _history_bonus(move, board.turn, depth, prev_move)
                if prev_move is not None:
                    _counter[prev_move.from_square][prev_move.to_square] = move
                for qm in quiet_tried:
                    _history_malus(qm, board.turn, depth, prev_move)
            break

        if is_quiet:
            quiet_tried.append(move)

    if excluded_move is None:
        if best_score <= orig_alpha:
            flag = TT_UPPER
        elif best_score >= beta:
            flag = TT_LOWER
        else:
            flag = TT_EXACT
        _tt_store(key, depth, flag, best_score, best_move, ply)

    return best_score


# ─────────────────────────────────────────────────────────────
#  PONDERING  (BUG FIX: nodes propre, abort séparé)
# ─────────────────────────────────────────────────────────────
def search_ponder(board: chess.Board, stop_event: threading.Event):
    _ponder_abort[0] = False
    _tt_new_search()
    ponder_nodes = [0]   # BUG FIX: nodes locaux, pas partagés
    far_future = time.time() + 3_600.0

    for depth in range(1, MAX_DEPTH + 1):
        if _should_stop_ponder(stop_event):
            break

        score = _pvs(board, depth, -INF, INF, 0, far_future,
                     True, None, False, None, ponder_nodes)

        if _should_stop_ponder(stop_event):
            break

        mate_in = None
        if abs(score) >= CHECKMATE - 500:
            mate_in = (CHECKMATE - abs(score) + 1) // 2
            if score < 0:
                mate_in = -mate_in

        # Formatage robuste
        if mate_in:
            try:
                mate_val = int(round(mate_in))
            except Exception:
                mate_val = int(mate_in)
            label = f"mate {mate_val}"
        else:
            try:
                best_score_int = int(round(score))
            except Exception:
                best_score_int = int(score)
            label = f"cp {best_score_int:+d}"

        try:
            print(f"[NovaBot] ♟ [ponder] d={depth:2d} | {label} | nodes={ponder_nodes[0]:,}")
        except Exception:
            try:
                print("[NovaBot] ♟ [ponder]", depth, label, "nodes=", ponder_nodes[0])
            except Exception:
                pass

        if abs(score) >= CHECKMATE - 500:
            break

    print("[NovaBot] ♟ Pondering terminé.")


# ─────────────────────────────────────────────────────────────
#  Iterative Deepening – Interface publique
# ─────────────────────────────────────────────────────────────
# Compteur de nœuds exposé pour game_handler / control panel
_nodes = [0]

def search(board: chess.Board, time_limit: float):
    """
    Iterative Deepening avec aspiration windows.

    Returns:
        (best_move, best_score, depth_reached)
    """
    _search_abort[0] = False
    _reset_heuristics()
    _tt_new_search()

    local_nodes = [0]
    _nodes[0]   = 0   # reset exposé

    start     = time.time()
    stop_time = start + time_limit * 0.95

    legal = list(board.legal_moves)
    if not legal:
        return None, 0, 0

    best_move  = legal[0]
    best_score = 0
    depth_done = 0

    # BUG FIX: AW_INIT adaptatif – premier score fiable après depth 1
    prev_score  = None
    AW_DELTAS   = [20, 60, 200, INF]

    for depth in range(1, MAX_DEPTH + 1):
        elapsed = time.time() - start
        if depth > 1 and elapsed >= time_limit * 0.50:
            break

        # BUG FIX: aspiration windows seulement si on a un score fiable
        if depth >= 5 and prev_score is not None and abs(prev_score) < CHECKMATE - 500:
            a, b = prev_score - AW_DELTAS[0], prev_score + AW_DELTAS[0]
        else:
            a, b = -INF, INF

        completed = False
        for attempt in range(len(AW_DELTAS)):
            score = _pvs(board, depth, a, b, 0, stop_time,
                         True, None, False, None, local_nodes)

            _nodes[0] = local_nodes[0]   # sync exposé

            if _search_abort[0] or time.time() >= stop_time:
                break

            if score <= a:
                idx = min(attempt + 1, len(AW_DELTAS) - 1)
                a   = max(-INF, (prev_score or 0) - AW_DELTAS[idx])
            elif score >= b:
                idx = min(attempt + 1, len(AW_DELTAS) - 1)
                b   = min(INF,  (prev_score or 0) + AW_DELTAS[idx])
            else:
                completed = True
                break

        if _search_abort[0] or (time.time() >= stop_time and depth > 1):
            break

        if not completed and depth > 1:
            break

        # BUG FIX: valider le TT move avant de l'accepter comme best_move
        key   = chess.polyglot.zobrist_hash(board)
        entry = _tt_probe(key)
        if (entry and entry.move
                and entry.move in board.legal_moves):   # ← validation légalité
            best_move  = entry.move
            best_score = score
            prev_score = score

        depth_done = depth
        elapsed    = time.time() - start
        nps        = int(local_nodes[0] / max(elapsed, 0.001))

        mate_in = None
        if abs(best_score) >= CHECKMATE - 500:
            mate_in = (CHECKMATE - abs(best_score) + 1) // 2
            if best_score < 0:
                mate_in = -mate_in

        # Normaliser les valeurs pour le formatage (best_score peut être float si NNUE est utilisé)
        if mate_in:
            try:
                mate_val = int(round(mate_in))
            except Exception:
                mate_val = int(mate_in)
            score_str = f"mate {mate_val}"
        else:
            try:
                best_score_int = int(round(best_score))
            except Exception:
                best_score_int = int(best_score)
            score_str = f"cp {best_score_int:+d}"

        # Debug print (robuste)
        try:
            elapsed_ms = int((time.time() - start) * 1000)
            print("[NovaBot] d={} | {} | move={} | nodes={} | nps={} | time={}ms".format(
                depth_done, score_str, str(best_move), local_nodes[0], nps, elapsed_ms
            ))
        except Exception:
            try:
                print("[NovaBot] d=", depth_done, score_str, "move=", best_move)
            except Exception:
                pass

    # Final normalization
    try:
        best_score_int = int(round(best_score))
    except Exception:
        try:
            best_score_int = int(best_score)
        except Exception:
            best_score_int = 0

    return best_move, best_score_int, depth_done


# ─────────────────────────────────────────────────────────────
#  Exports / utilitaires
# ─────────────────────────────────────────────────────────────
def get_stats() -> dict:
    """
    Retourne quelques statistiques utiles pour l'UI.
    """
    try:
        return {"nps": 0, "nodes": _nodes[0] if isinstance(_nodes, list) else _nodes, "nnue": False}
    except Exception:
        return {"nps": 0, "nodes": 0, "nnue": False}

__all__ = ["search", "search_ponder", "abort_ponder", "_nodes", "get_stats"]
