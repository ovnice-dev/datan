# =============================================================
#  NovaBot – NNUE HalfKP  (version avancée)
#
#  Architecture : HalfKP features → 1024 → 128 → 1
#
#  HalfKP features (comme Stockfish NNUE) :
#    Pour chaque roi (blanc + noir) :
#      Pour chaque pièce sur l'échiquier (pas les rois) :
#        64 cases × 10 types de pièces × 64 cases roi = 40 960 features
#    Total input : 40 960 (au lieu de 768 en version basique)
#
#  Pourquoi HalfKP est bien meilleur :
#    - Encode la RELATION entre le roi et chaque pièce
#    - Le réseau apprend "cette pièce est dangereuse POUR CE ROI"
#    - Impossible à capturer avec 768 features basiques
#
#  Gain estimé vs 768 features : +150 à +200 ELO
# =============================================================
import chess
import os
import math

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# ─────────────────────────────────────────────────────────────
#  Constantes HalfKP
# ─────────────────────────────────────────────────────────────
# 64 cases roi × 64 cases pièce × 10 types (5 pièces × 2 couleurs, sans roi)
KING_SQUARES  = 64
PIECE_SQUARES = 64
PIECE_TYPES   = 10   # pion_b, cavalier_b, fou_b, tour_b, dame_b × 2 couleurs

INPUT_SIZE  = KING_SQUARES * PIECE_SQUARES * PIECE_TYPES  # = 40 960
HIDDEN1     = 1024   # grosse première couche
HIDDEN2     = 128
NNUE_SCALE  = 600
EVAL_CLAMP  = 29_000

_EXPECTED_SHAPES = {
    "w1": (INPUT_SIZE, HIDDEN1),
    "b1": (HIDDEN1,),
    "w2": (HIDDEN1, HIDDEN2),
    "b2": (HIDDEN2,),
    "w3": (HIDDEN2, 1),
    "b3": (1,),
}

# Mapping type de pièce → index 0..9
# (depuis la perspective du joueur courant)
_PIECE_INDEX = {
    (chess.PAWN,   True):  0,   # pion ami
    (chess.KNIGHT, True):  1,
    (chess.BISHOP, True):  2,
    (chess.ROOK,   True):  3,
    (chess.QUEEN,  True):  4,
    (chess.PAWN,   False): 5,   # pion adverse
    (chess.KNIGHT, False): 6,
    (chess.BISHOP, False): 7,
    (chess.ROOK,   False): 8,
    (chess.QUEEN,  False): 9,
}


# ─────────────────────────────────────────────────────────────
#  HalfKP Feature extraction
# ─────────────────────────────────────────────────────────────
def _halfkp_features(board: chess.Board) -> np.ndarray:
    """
    Extrait les features HalfKP pour la position courante.
    Retourne un vecteur (40960,) float32.

    Pour chaque pièce (hors rois) :
      feat_idx = king_sq * 640 + piece_type_idx * 64 + piece_sq
      où king_sq est la case du roi du joueur courant (perspective STM)
    """
    if not _HAS_NUMPY:
        raise ImportError("numpy requis")

    features = np.zeros(INPUT_SIZE, dtype=np.float32)
    stm      = board.turn   # Side To Move
    flip     = (stm == chess.BLACK)

    king_sq = board.king(stm)
    if king_sq is None:
        return features

    # Si noirs au trait, miroir vertical pour avoir perspective cohérente
    if flip:
        king_sq = chess.square_mirror(king_sq)

    for piece_type in (chess.PAWN, chess.KNIGHT, chess.BISHOP,
                       chess.ROOK, chess.QUEEN):
        for color in (chess.WHITE, chess.BLACK):
            is_friendly = (color == stm)
            pt_idx = _PIECE_INDEX.get((piece_type, is_friendly))
            if pt_idx is None:
                continue

            for sq in board.pieces(piece_type, color):
                piece_sq = chess.square_mirror(sq) if flip else sq
                feat_idx = king_sq * (PIECE_SQUARES * PIECE_TYPES) + pt_idx * PIECE_SQUARES + piece_sq
                if 0 <= feat_idx < INPUT_SIZE:
                    features[feat_idx] = 1.0

    return features


# ─────────────────────────────────────────────────────────────
#  Classe NNUE HalfKP
# ─────────────────────────────────────────────────────────────
class NNUE:
    def __init__(self, w1, b1, w2, b2, w3, b3):
        if not _HAS_NUMPY:
            raise ImportError("numpy requis pour NNUE")
        self.w1 = np.array(w1, dtype=np.float32)
        self.b1 = np.array(b1, dtype=np.float32)
        self.w2 = np.array(w2, dtype=np.float32)
        self.b2 = np.array(b2, dtype=np.float32)
        self.w3 = np.array(w3, dtype=np.float32)
        self.b3 = np.array(b3, dtype=np.float32)

    def _check_shapes(self):
        for name, expected in _EXPECTED_SHAPES.items():
            arr = getattr(self, name)
            if arr.shape != expected:
                raise ValueError(
                    f"NNUE shape mismatch pour {name}: "
                    f"attendu {expected}, eu {arr.shape}"
                )

    def forward(self, features: np.ndarray) -> float:
        h1  = np.clip(features @ self.w1 + self.b1, 0.0, 1.0)
        h2  = np.clip(h1       @ self.w2 + self.b2, 0.0, 1.0)
        out = float((h2 @ self.w3 + self.b3)[0])
        return out

    def evaluate(self, board: chess.Board) -> int:
        feats = _halfkp_features(board)
        raw   = self.forward(feats)
        score = raw * NNUE_SCALE
        return int(max(-EVAL_CLAMP, min(EVAL_CLAMP, score)))

    @staticmethod
    def load(path: str) -> "NNUE":
        if not _HAS_NUMPY:
            raise ImportError("numpy requis pour NNUE")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Fichier NNUE introuvable : {path}")
        data    = np.load(path)
        missing = {"w1","b1","w2","b2","w3","b3"} - set(data.files)
        if missing:
            raise KeyError(f"Clés manquantes dans le .npz : {missing}")
        net = NNUE(data["w1"], data["b1"],
                   data["w2"], data["b2"],
                   data["w3"], data["b3"])
        net._check_shapes()
        return net

    def save(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        np.savez(path,
                 w1=self.w1, b1=self.b1,
                 w2=self.w2, b2=self.b2,
                 w3=self.w3, b3=self.b3)
        print(f"[NNUE] ✓ Poids sauvegardés → {path}")

    @staticmethod
    def new_random() -> "NNUE":
        if not _HAS_NUMPY:
            raise ImportError("numpy requis")

        def he(fan_in, fan_out):
            std = math.sqrt(2.0 / fan_in)
            return np.random.randn(fan_in, fan_out).astype(np.float32) * std

        def bias(size):
            return np.full(size, 0.01, dtype=np.float32)

        return NNUE(
            he(INPUT_SIZE, HIDDEN1), bias(HIDDEN1),
            he(HIDDEN1, HIDDEN2),   bias(HIDDEN2),
            he(HIDDEN2, 1),         np.zeros(1, dtype=np.float32),
        )


# ─────────────────────────────────────────────────────────────
#  Instance globale
# ─────────────────────────────────────────────────────────────
_nnue_instance: NNUE | None = None

def load_global(path: str) -> bool:
    global _nnue_instance
    try:
        _nnue_instance = NNUE.load(path)
        print(f"[NNUE] ✓ Modèle HalfKP chargé : {path}")
        print(f"[NNUE]   Architecture : {INPUT_SIZE}→{HIDDEN1}→{HIDDEN2}→1")
        return True
    except Exception as e:
        print(f"[NNUE] ✗ Chargement échoué ({e}) → HCE actif")
        _nnue_instance = None
        return False

def is_loaded() -> bool:
    return _nnue_instance is not None

def evaluate_nnue(board: chess.Board):
    if _nnue_instance is None:
        return None
    try:
        stm_score = _nnue_instance.evaluate(board)
        return stm_score if board.turn == chess.WHITE else -stm_score
    except Exception as e:
        print(f"[NNUE] ⚠ Erreur inférence ({e}) → HCE")
        return None

# Export pour trainer
def _extract_features_np(board: chess.Board) -> np.ndarray:
    return _halfkp_features(board)