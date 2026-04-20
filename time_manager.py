# =============================================================
# NovaBot – Gestion du temps (Étape 3 – bullet/blitz safe)
# =============================================================
from config import (
    TIME_SAFETY_MARGIN,
    MIN_THINK_TIME,
    MAX_THINK_RATIO,
    INCREMENT_BONUS,
    NETWORK_LATENCY_S,
)

def compute_think_time(
    my_time_ms: int,
    opponent_time_ms: int,
    increment_ms: int,
    fullmove_number: int,
    is_endgame: bool = False,
    move_importance: float = 1.0,
) -> float:
    """
    Calcule le temps de réflexion optimal (en secondes).

    Logique :
    ─────────
    1. Détecte le contrôle de temps (bullet / blitz / rapid) et adapte
       les paramètres en conséquence.
    2. Calcule le nombre estimé de coups restants (modèle empirique).
    3. Répartit le temps restant uniformément, avec bonus incrément.
    4. Applique un plafond strict anti-flag ET déduit la latence réseau.
    5. En finale ou position critique, alloue un peu plus.
    6. Si l'adversaire est en zeitnot on peut jouer plus vite.
    """
    if my_time_ms <= 0:
        return MIN_THINK_TIME

    my_s   = my_time_ms   / 1000.0
    opp_s  = opponent_time_ms / 1000.0
    incr_s = increment_ms / 1000.0

    # ── Détection du contrôle de temps ───────────────────────
    # On estime le temps total initial (rough) : temps actuel + coups joués * incrément
    estimated_total = my_s + fullmove_number * incr_s
    is_bullet  = estimated_total < 120          # < 2 min total estimé
    is_blitz   = 120 <= estimated_total < 600   # 2–10 min

    # ── Marge de sécurité adaptative ─────────────────────────
    if is_bullet:
        safety_ratio = max(TIME_SAFETY_MARGIN, 0.15)   # 15 % min en bullet
    elif is_blitz:
        safety_ratio = max(TIME_SAFETY_MARGIN, 0.10)   # 10 % min en blitz
    else:
        safety_ratio = TIME_SAFETY_MARGIN               # config par défaut

    safety = my_s * safety_ratio

    # ── Coups restants estimés ────────────────────────────────
    if fullmove_number < 10:
        moves_left = 45
    elif fullmove_number < 25:
        moves_left = 35
    elif fullmove_number < 40:
        moves_left = 25
    elif is_endgame:
        moves_left = 18
    else:
        moves_left = 22

    # ── Temps de base ─────────────────────────────────────────
    incr_bonus = INCREMENT_BONUS
    if is_bullet:
        incr_bonus = min(INCREMENT_BONUS, 0.50)  # on capitalise moins sur l'incrément
    elif is_blitz:
        incr_bonus = min(INCREMENT_BONUS, 0.65)

    base = (my_s - safety) / moves_left + incr_s * incr_bonus

    # ── Modulation selon l'importance du coup ─────────────────
    base *= move_importance

    # ── Bonus si l'adversaire est en zeitnot ──────────────────
    # → on peut se permettre de penser un tout petit peu plus
    if opp_s < my_s * 0.3:
        base *= 1.10   # modeste : on reste prudent en bullet

    # ── Plafonds stricts ──────────────────────────────────────
    if is_bullet:
        hard_cap_ratio = 0.12   # jamais > 12 % du temps restant en bullet
    elif is_blitz:
        hard_cap_ratio = 0.18
    else:
        hard_cap_ratio = 0.25

    hard_cap  = my_s * hard_cap_ratio
    ratio_cap = my_s * MAX_THINK_RATIO * (1.4 if is_endgame else 1.0)

    think_time = min(base, hard_cap, ratio_cap)

    # ── Déduction de la latence réseau (HF Spaces → Lichess) ─
    think_time -= NETWORK_LATENCY_S
    think_time = max(think_time, MIN_THINK_TIME)

    return think_time