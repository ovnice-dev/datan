# config.py
"""
NovaBot – Configuration (Étape 6 – bullet/blitz safe)
Fichier nettoyé : chemins résolus, override par variables d'environnement,
POLYGLOT_PATH en liste, USE_NNUE déterminé automatiquement si possible.
"""

from pathlib import Path
import os

# Base
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"

# ── Lichess ──────────────────────────────────────────────────
LICHESS_TOKEN = os.environ.get("LICHESS_TOKEN", "")

# ── PyPy3 (optionnel) ───────────────────────────────────────
PYPY_PATH = os.environ.get("PYPY_PATH", r"C:\pypy3.11-v7.3.21-win64\pypy3.exe")

# ── Moteur ───────────────────────────────────────────────────
MAX_DEPTH = 64
CONTEMPT = 15
TT_SIZE = 1 << 23  # 8 388 608 entrées (puissance de 2 obligatoire)

# ── NNUE ─────────────────────────────────────────────────────
# Nom de fichier attendu dans data/
DEFAULT_NNUE_FILENAME = "novabot.nnue.npz"
NNUE_PATH = Path(os.environ.get("NNUE_PATH", "")) if os.environ.get("NNUE_PATH") else (DATA_DIR / DEFAULT_NNUE_FILENAME)

# POLYGLOT_PATH peut être une chaîne séparée par des virgules ou une liste d'entrées
_polygot_env = os.environ.get("POLYGLOT_PATH", "")
if _polygot_env:
    POLYGLOT_PATH = [Path(p.strip()) for p in _polygot_env.split(",") if p.strip()]
else:
    # chemins relatifs dans data/
    POLYGLOT_PATH = [DATA_DIR / p for p in ("komodo.bin", "rodent.bin", "gm2001.bin")]

# USE_NNUE : priorité à la variable d'environnement, sinon détection automatique
_env_use_nnue = os.environ.get("USE_NNUE")
if _env_use_nnue is not None:
    USE_NNUE = _env_use_nnue.lower() in ("1", "true", "yes", "on")
else:
    USE_NNUE = NNUE_PATH.exists()

# ── Entraînement NNUE ────────────────────────────────────────
PGN_PATH = Path(os.environ.get("PGN_PATH", r"C:\Users\SMILE\Downloads\lichess_db_standard_rated_2016-01.pgn.zst"))
DATASET_CSV = os.environ.get("DATASET_CSV", "training_data.csv")
MIN_ELO = int(os.environ.get("MIN_ELO", 2000))
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", 5))
MAX_POSITIONS = int(os.environ.get("MAX_POSITIONS", 2_000_000))
TRAIN_EPOCHS = int(os.environ.get("TRAIN_EPOCHS", 10))

# ── Pruning / Réductions ─────────────────────────────────────
NULL_MOVE_MIN_DEPTH = 3
LMR_MIN_DEPTH = 3
LMR_MIN_MOVES = 3
FUTILITY_MARGIN = 120
DELTA_PRUNING_MARGIN = 200
RAZOR_MARGIN = 300
IIR_MIN_DEPTH = 4

# ── Extensions ───────────────────────────────────────────────
SINGULAR_DEPTH_MIN = 6
SINGULAR_MARGIN = 2
CHECK_EXT_SEE_THRESH = 0

# ── Gestion du temps ─────────────────────────────────────────
TIME_SAFETY_MARGIN = float(os.environ.get("TIME_SAFETY_MARGIN", 0.08))
MIN_THINK_TIME = float(os.environ.get("MIN_THINK_TIME", 0.05))
MAX_THINK_RATIO = float(os.environ.get("MAX_THINK_RATIO", 0.05))
INCREMENT_BONUS = float(os.environ.get("INCREMENT_BONUS", 0.65))
NETWORK_LATENCY_S = float(os.environ.get("NETWORK_LATENCY_S", 0.30))

# ── Défis ────────────────────────────────────────────────────
ACCEPT_VARIANTS = ["standard"]
ACCEPT_TIME_CTRL = ["bullet", "blitz", "rapid", "classical", "correspondence"]

# --- Vérifications simples (non bloquantes) -------------------
# S'assurer que DATA_DIR existe (utile pour debug local)
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# Vérification basique TT_SIZE (doit être puissance de 2)
def _is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0

if not _is_power_of_two(TT_SIZE):
    raise ValueError("TT_SIZE doit être une puissance de 2")

# Exports utiles pour le reste du code
__all__ = [
    "BASE_DIR", "DATA_DIR", "LICHESS_TOKEN", "PYPY_PATH",
    "MAX_DEPTH", "CONTEMPT", "TT_SIZE",
    "USE_NNUE", "NNUE_PATH", "POLYGLOT_PATH",
    "PGN_PATH", "DATASET_CSV", "MIN_ELO", "SAMPLE_RATE", "MAX_POSITIONS", "TRAIN_EPOCHS",
    "NULL_MOVE_MIN_DEPTH", "LMR_MIN_DEPTH", "LMR_MIN_MOVES", "FUTILITY_MARGIN",
    "DELTA_PRUNING_MARGIN", "RAZOR_MARGIN", "IIR_MIN_DEPTH",
    "SINGULAR_DEPTH_MIN", "SINGULAR_MARGIN", "CHECK_EXT_SEE_THRESH",
    "TIME_SAFETY_MARGIN", "MIN_THINK_TIME", "MAX_THINK_RATIO", "INCREMENT_BONUS", "NETWORK_LATENCY_S",
    "ACCEPT_VARIANTS", "ACCEPT_TIME_CTRL"
]
