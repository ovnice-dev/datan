# engine/evaluate.py
# Shim + full Python evaluation implementation.
# Tries to load a compiled extension module (engine.evaluate or engine._evaluate_c).
# If the compiled module exposes `evaluate` or `HCEEngine`, the shim will use it.
# Otherwise it falls back to the pure-Python evaluator (material + PST + phase).
#
# Exposes:
#   - evaluate(board) -> int  (centipawns)
#   - is_compiled() -> bool

from pathlib import Path
import importlib
import importlib.util
import importlib.machinery
import sys
import traceback
import chess

_THIS_DIR = Path(__file__).parent

# ---------------------------------------------------------------------
# Try to import a compiled extension module first (preferred names)
# ---------------------------------------------------------------------
_ext_mod = None
_IS_COMPILED = False

_CANDIDATE_MODULES = [
    "engine.evaluate",      # if built in-place as engine.evaluate
    "engine._evaluate_c",   # recommended compiled module name
    "engine.evaluate_c",
    "engine.evaluate_ext",
]

for modname in _CANDIDATE_MODULES:
    try:
        _ext_mod = importlib.import_module(modname)
        _IS_COMPILED = True
        break
    except Exception:
        _ext_mod = None

# If not found via import, try to locate extension files in engine/ and load them directly.
if _ext_mod is None:
    _PY_EXT_CANDIDATES = []
    if sys.platform == "win32":
        _PY_EXT_CANDIDATES += [
            _THIS_DIR / f"evaluate.cp{sys.version_info.major}{sys.version_info.minor}-win_amd64.pyd",
            _THIS_DIR / "evaluate.pyd",
            _THIS_DIR / "_evaluate_c.pyd",
        ]
    else:
        _PY_EXT_CANDIDATES += [
            _THIS_DIR / "evaluate.so",
            _THIS_DIR / "_evaluate_c.so",
        ]
    for p in _PY_EXT_CANDIDATES:
        try:
            if p is not None and p.exists():
                loader = importlib.machinery.ExtensionFileLoader(p.stem, str(p))
                spec = importlib.util.spec_from_loader(p.stem, loader)
                mod = importlib.util.module_from_spec(spec)
                loader.exec_module(mod)
                _ext_mod = mod
                _IS_COMPILED = True
                break
        except Exception:
            _ext_mod = None

# ---------------------------------------------------------------------
# If compiled module is present and exposes evaluate or HCEEngine, wire it up.
# ---------------------------------------------------------------------
if _ext_mod is not None:
    try:
        if hasattr(_ext_mod, "evaluate"):
            evaluate = getattr(_ext_mod, "evaluate")
        elif hasattr(_ext_mod, "HCEEngine"):
            # Provide a wrapper that reuses a single HCEEngine instance for performance.
            _HCE_INST = None

            def _get_hce_instance():
                global _HCE_INST
                if _HCE_INST is None:
                    _HCE_INST = _ext_mod.HCEEngine()
                return _HCE_INST

            def evaluate(board):
                inst = _get_hce_instance()
                # HCEEngine may expect a FEN string; try Board first, fallback to str(board)
                try:
                    if isinstance(board, chess.Board):
                        return inst.evaluate_fen(board.fen())
                    else:
                        return inst.evaluate_fen(str(board))
                except AttributeError:
                    # If evaluate_fen is not available, try a generic evaluate method
                    try:
                        return inst.evaluate(board)
                    except Exception:
                        raise
        else:
            # compiled module present but doesn't expose expected API
            _IS_COMPILED = False
            _ext_mod = None
    except Exception:
        # If anything goes wrong, fall back to Python implementation
        _IS_COMPILED = False
        _ext_mod = None

# ---------------------------
# Fallback pure-Python evaluator
# ---------------------------
if _ext_mod is None:
    # Try to load NNUE Python wrapper if available (optional)
    try:
        from config import USE_NNUE, NNUE_PATH
    except Exception:
        USE_NNUE = False
        NNUE_PATH = None

    try:
        from engine.nnue import evaluate_nnue, load_global as _nnue_load
        if USE_NNUE and NNUE_PATH:
            try:
                _nnue_load(NNUE_PATH)
            except Exception:
                pass
    except Exception:
        def evaluate_nnue(board):
            return None

    # Piece values MG / EG
    _MG = [0, 100, 320, 330, 500, 960, 0]
    _EG = [0, 120, 290, 310, 520, 940, 0]

    def _mk(raw):
        out = [0] * 64
        for rank in range(8):
            for file in range(8):
                out[chess.square(file, rank)] = raw[(7 - rank) * 8 + file]
        return out

    _PST_MG = [None,
        _mk([ 0,  0,  0,  0,  0,  0,  0,  0,
             98,134, 61, 95, 68,126, 34,-11,
             -6,  7, 26, 31, 65, 56, 25,-20,
            -14, 13,  6, 21, 23, 12, 17,-23,
            -27, -2, -5, 12, 17,  6, 10,-25,
            -26, -4, -4,-10,  3,  3, 33,-12,
            -35, -1,-20,-23,-15, 24, 38,-22,
              0,  0,  0,  0,  0,  0,  0,  0]),
        _mk([-167,-89,-34,-49, 61,-97,-15,-107,
             -73,-41, 72, 36, 23, 62,  7, -17,
             -47, 60, 37, 65, 84,129, 73,  44,
              -9, 17, 19, 53, 37, 69, 18,  22,
             -13,  4, 16, 13, 28, 19, 21,  -8,
             -23, -9, 12, 10, 19, 17, 25, -16,
             -29,-53,-12, -3, -1, 18,-14, -19,
            -105,-21,-58,-33,-17,-28,-19, -23]),
        _mk([-29,  4,-82,-37,-25,-42,  7, -8,
             -26, 16,-18,-13, 30, 59, 18,-47,
             -16, 37, 43, 40, 35, 50, 37, -2,
              -4,  5, 19, 50, 37, 37,  7, -2,
              -6, 13, 13, 26, 34, 12, 10,  4,
               0, 15, 15, 15, 14, 27, 18, 10,
               4, 15, 16,  0,  7, 21, 33,  1,
             -33, -3,-14,-21,-13,-12,-39,-21]),
        _mk([ 32, 42, 32, 51, 63,  9, 31, 43,
              27, 32, 58, 62, 80, 67, 26, 44,
              -5, 19, 26, 36, 17, 45, 61, 16,
             -24,-11,  7, 26, 24, 35, -8,-20,
             -36,-26,-12, -1,  9, -7,  6,-23,
             -45,-25,-16,-17,  3,  0, -5,-33,
             -44,-16,-20, -9, -1, 11, -6,-71,
             -19,-13,  1, 17, 16,  7,-37,-26]),
        _mk([-28,  0, 29, 12, 59, 44, 43, 45,
             -24,-39, -5,  1,-16, 57, 28, 54,
             -13,-17,  7,  8, 29, 56, 47, 57,
             -27,-27,-16,-16, -1, 17, -2,  1,
              -9,-26, -9,-10, -2, -4,  3, -3,
             -14,  2,-11, -2, -5,  2, 14,  5,
             -35, -8, 11,  2,  8, 15, -3,  1,
              -1,-18, -9, 10,-15,-25,-31,-50]),
        _mk([-65, 23, 16,-15,-56,-34,  2, 13,
              29, -1,-20, -7, -8, -4,-38,-29,
              -9, 24,  2,-16,-20,  6, 22,-22,
             -17,-20,-12,-27,-30,-25,-14,-36,
             -49, -1,-27,-39,-46,-44,-33,-51,
             -14,-14,-22,-46,-44,-30,-15,-27,
               1,  7, -8,-64,-43,-16,  9,  8,
             -15, 36, 12,-54,  8,-28, 24, 14]),
    ]

    _PST_EG = [None,
        _mk([  0,  0,  0,  0,  0,  0,  0,  0,
              178,173,158,134,147,132,165,187,
               94,100, 85, 67, 56, 53, 82, 84,
               32, 24, 13,  5, -2,  4, 17, 17,
               13,  9, -3, -7, -7, -8,  3, -1,
                4,  7, -6,  1,  0, -5, -1, -8,
               13,  8,  8, 10, 13,  0,  2, -7,
                0,  0,  0,  0,  0,  0,  0,  0]),
        _mk([-58,-38,-13,-28,-31,-27,-63,-99,
             -25, -8,-25, -2, -9,-25,-24,-52,
             -24,-20, 10,  9, -1, -9,-19,-41,
             -17,  3, 22, 22, 22, 11,  8,-18,
             -18, -6, 16, 25, 16, 17,  4,-18,
             -23, -3, -1, 15, 10, -3,-20,-22,
             -42,-20,-10, -5, -2,-20,-23,-44,
             -29,-51,-23,-15,-22,-18,-50,-64]),
        _mk([-14,-21,-11, -8, -7, -9,-17,-24,
              -8, -4,  7,-12, -3,-13, -4,-14,
               2, -8,  0, -1, -2,  6,  0,  4,
              -3,  9, 12,  9, 14, 10,  3,  2,
              -6,  3, 13, 19,  7, 10, -3, -9,
             -12, -3,  8, 10, 13,  3, -7,-15,
             -14,-18, -7, -1,  4, -9,-15,-27,
             -23, -9,-23, -5, -9,-16, -5,-17]),
        _mk([ 13, 10, 18, 15, 12, 12,  8,  5,
              11, 13, 13, 11, -3,  3,  8,  3,
               7,  7,  7,  5,  4, -3, -5, -3,
               4,  3, 13,  1,  2,  1, -1,  2,
               3,  5,  8,  4, -5, -6, -8,-11,
              -4,  0, -5, -1, -7,-12, -8,-16,
              -6, -6,  0,  2, -9, -9,-11, -3,
              -9,  2,  3, -1, -5,-13,  4,-20]),
        _mk([ -9, 22, 22, 27, 27, 19, 10, 20,
              -17, 20, 32, 41, 58, 25, 30,  0,
              -20,  6,  9, 49, 47, 35, 19,  9,
                3, 22, 24, 45, 57, 40, 57, 36,
              -18, 28, 19, 47, 31, 34, 39, 23,
              -16,-27, 15,  6,  9, 17, 10,  5,
              -22,-23,-30,-16,-16,-23,-36,-32,
              -33,-28,-22,-43, -5,-32,-20,-41]),
        _mk([-74,-35,-18,-18,-11, 15,  4,-17,
             -12, 17, 14, 17, 17, 38, 23, 11,
              10, 17, 23, 15, 20, 45, 44, 13,
              -8, 22, 24, 27, 26, 33, 26,  3,
             -18, -4, 21, 24, 27, 23,  9,-11,
             -19, -3, 11, 21, 23, 16,  7, -9,
             -27,-11,  4, 13, 14,  4, -5,-17,
             -53,-34,-21,-11,-28,-14,-24,-43]),
    ]

    _BISHOP_PAIR_MG = 30
    _BISHOP_PAIR_EG = 50

    _PHASE_W = [0, 0, 1, 1, 2, 4, 0]
    _MAX_PHASE = 24

    def _phase_factor(board: chess.Board) -> float:
        phase = 0
        for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
            phase += (len(board.pieces(pt, chess.WHITE))
                    + len(board.pieces(pt, chess.BLACK))) * _PHASE_W[pt]
        return 1.0 - min(phase, _MAX_PHASE) / _MAX_PHASE

    def evaluate(board: chess.Board) -> int:
        # Terminals
        try:
            if board.is_checkmate():
                return -30000 if board.turn == chess.WHITE else 30000
            if (board.is_stalemate()
                    or board.is_insufficient_material()
                    or board.is_seventyfive_moves()):
                return 0
        except Exception:
            return 0

        # NNUE short-circuit if available
        try:
            nnue_score = evaluate_nnue(board)
        except Exception:
            nnue_score = None
        if nnue_score is not None:
            return max(-29000, min(29000, int(nnue_score)))

        ef = _phase_factor(board)
        mef = 1.0 - ef

        mg = 0
        eg = 0

        # White pieces
        for pt in range(1, 7):
            pst_mg = _PST_MG[pt]
            pst_eg = _PST_EG[pt]
            mg_val = _MG[pt]
            eg_val = _EG[pt]
            for sq in board.pieces(pt, chess.WHITE):
                mg += mg_val + pst_mg[sq]
                eg += eg_val + pst_eg[sq]

        # Black pieces (mirror PST)
        for pt in range(1, 7):
            pst_mg = _PST_MG[pt]
            pst_eg = _PST_EG[pt]
            mg_val = _MG[pt]
            eg_val = _EG[pt]
            for sq in board.pieces(pt, chess.BLACK):
                msq = sq ^ 56
                mg -= mg_val + pst_mg[msq]
                eg -= eg_val + pst_eg[msq]

        if len(board.pieces(chess.BISHOP, chess.WHITE)) >= 2:
            mg += _BISHOP_PAIR_MG
            eg += _BISHOP_PAIR_EG
        if len(board.pieces(chess.BISHOP, chess.BLACK)) >= 2:
            mg -= _BISHOP_PAIR_MG
            eg -= _BISHOP_PAIR_EG

        return int(mg * mef + eg * ef)

# End fallback implementation

def is_compiled() -> bool:
    """Return True if a compiled extension was successfully loaded."""
    return _IS_COMPILED
