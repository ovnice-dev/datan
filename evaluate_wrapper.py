# engine/evaluate_wrapper.py
"""
Wrapper compatible pour fournir une fonction `evaluate(fen)` partout dans le projet.
Si le module compilé engine.evaluate expose HCEEngine, on l'utilise.
"""

try:
    from engine.evaluate import HCEEngine
except Exception:
    HCEEngine = None

def evaluate(fen: str):
    if HCEEngine is None:
        raise RuntimeError("engine.evaluate non disponible (HCEEngine introuvable).")
    inst = HCEEngine()
    return inst.evaluate_fen(fen)
