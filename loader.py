# engine/loader.py
from pathlib import Path
import threading
import importlib
import traceback

# config doit être à la racine du projet
try:
    from config import NNUE_PATH, USE_NNUE
except Exception:
    # fallback si config introuvable
    NNUE_PATH = Path("data/novabot.nnue.npz")
    USE_NNUE = False

# état interne
_engine = None
_engine_lock = threading.Lock()
_stats = {"nps": 0, "ponder": False, "book_move": None, "nnue": bool(USE_NNUE)}

def _log(msg: str):
    # simple logger : le panel récupère les messages via queue, mais on print pour debug console
    try:
        print(f"[engine.loader] {msg}")
    except Exception:
        pass

def start_engine():
    """
    Initialise _engine : essaie NNUE si USE_NNUE True, sinon HCE.
    Compatible avec deux signatures de NNUE :
      - NNUE(npz_path)
      - NNUE(w1,b1,w2,b2) ou NNUE(w1,b1,w2,b2,w3,b3)
    """
    global _engine, _stats
    with _engine_lock:
        if _engine is not None:
            _log("Engine déjà démarré.")
            return

        # tentative d'import des modules compilés
        try:
            nnue_mod = importlib.import_module("engine.nnue")
        except Exception:
            nnue_mod = None

        try:
            eval_mod = importlib.import_module("engine.evaluate")
        except Exception:
            eval_mod = None

        if USE_NNUE and nnue_mod is not None and Path(str(NNUE_PATH)).exists():
            _log(f"NNUE demandé et fichier trouvé : {NNUE_PATH}")
            # 1) essayer constructeur simple (npz path)
            try:
                _engine = nnue_mod.NNUE(str(NNUE_PATH))
                _stats["nnue"] = True
                _log("NNUE instancié via chemin .npz")
                return
            except TypeError:
                _log("Constructeur NNUE ne prend pas (npz_path) — tentative avec arrays.")
            except Exception as e:
                _log(f"Erreur instanciation NNUE (chemin) : {e}")
                _log(traceback.format_exc())

            # 2) essayer de charger le .npz et passer les arrays (ancienne API)
            try:
                import numpy as np
                data = np.load(str(NNUE_PATH), allow_pickle=False)
                # noms usuels : w1,b1,w2,b2,(w3,b3)
                w1 = data.get("w1", None)
                b1 = data.get("b1", None)
                w2 = data.get("w2", None)
                b2 = data.get("b2", None)
                w3 = data.get("w3", None)
                b3 = data.get("b3", None)

                # vérification minimale
                if w1 is None or b1 is None or w2 is None or b2 is None:
                    raise ValueError("Fichier .npz manquant des clés w1/b1/w2/b2")

                # appel flexible du constructeur
                try:
                    if w3 is None or b3 is None:
                        _engine = nnue_mod.NNUE(w1, b1, w2, b2)
                    else:
                        _engine = nnue_mod.NNUE(w1, b1, w2, b2, w3, b3)
                    _stats["nnue"] = True
                    _log("NNUE instancié via arrays extraits du .npz")
                    return
                except Exception as e:
                    _log(f"Erreur instanciation NNUE (arrays) : {e}")
                    _log(traceback.format_exc())
            except Exception as e:
                _log(f"Erreur lecture .npz ou préparation arrays : {e}")
                _log(traceback.format_exc())

        # Fallback HCE
        if eval_mod is not None:
            try:
                HCEEngine = getattr(eval_mod, "HCEEngine", None)
                if HCEEngine is None:
                    raise RuntimeError("HCEEngine introuvable dans engine.evaluate")
                _engine = HCEEngine()
                _stats["nnue"] = False
                _log("Fallback HCE initialisé.")
                return
            except Exception as e:
                _log(f"Impossible d'initialiser HCEEngine: {e}")
                _log(traceback.format_exc())

        # Si on arrive ici, on n'a rien pu initialiser
        raise RuntimeError("Aucun moteur disponible (NNUE et HCE ont échoué).")

def stop_engine():
    """Arrête et nettoie le moteur."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _log("stop_engine appelé mais aucun engine actif.")
            return
        try:
            _engine.shutdown()
        except Exception:
            _log("Erreur lors du shutdown du moteur (ignorée).")
        _engine = None
        _log("Engine arrêté.")

def get_stats():
    """
    Retourne un dict non-bloquant avec les stats actuelles.
    Doit être rapide pour le panel.
    """
    global _stats
    if _engine is None:
        return _stats
    try:
        s = _engine.get_runtime_stats()
        if isinstance(s, dict):
            _stats.update(s)
    except Exception:
        # ne pas lever pour garder UI réactive
        _log("Erreur get_runtime_stats (ignorée).")
    return _stats

def test_evaluate():
    """Appel simple pour vérifier que l'engine répond."""
    with _engine_lock:
        if _engine is None:
            try:
                start_engine()
            except Exception as e:
                return f"start_engine failed: {e}"
        try:
            return _engine.simple_test()
        except Exception as e:
            return f"test_evaluate error: {e}"
