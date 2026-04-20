"""
NovaBot – Point d'entrée principal

  Mode 1 : Terminal interactif (Windows local)
    → Lance control_panel.py (interface console ASCII)

  Mode 2 : Serveur avec dashboard web (Render / Railway)
    → Lance Flask dashboard sur $PORT
    → Le bot démarre automatiquement
    → Accessible depuis n'importe où via navigateur

  Mode 3 : Force dashboard local (python main.py --web)
    → Dashboard sur http://localhost:5000
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def is_interactive():
    """True si lancé depuis un vrai terminal (Windows local)."""
    return sys.stdin.isatty()


def is_server():
    """True si on est dans un environnement serveur (Render, Railway, etc.)."""
    return (
        os.environ.get("RENDER") == "true"
        or os.environ.get("RAILWAY_ENVIRONMENT") is not None
        or os.environ.get("PORT") is not None   # Render/Railway injectent PORT
    )


# ── Argument --web ─────────────────────────────────────────────
force_web = "--web" in sys.argv or "-w" in sys.argv


if force_web:
    # Mode 3 : dashboard local forcé
    print("[NovaBot] Mode dashboard local → http://localhost:5000")
    from dashboard import run_dashboard
    run_dashboard(host="127.0.0.1", port=5000, auto_start_bot=False)

elif is_server():
    # Mode 2 : serveur distant (Render / Railway)
    port = int(os.environ.get("PORT", 5000))
    print(f"[NovaBot] Mode serveur détecté → Dashboard sur port {port}")
    from dashboard import run_dashboard
    run_dashboard(host="0.0.0.0", port=port, auto_start_bot=True)

elif is_interactive():
    # Mode 1 : terminal Windows local → control panel console
    if os.name == "nt":
        os.system("chcp 65001 >nul 2>&1")
        os.system("color")
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 0x0007)
            k.SetConsoleMode(k.GetStdHandle(-10), 0x0007)
        except Exception:
            pass
    try:
        from control_panel import menu_principal
        menu_principal()
    except KeyboardInterrupt:
        print("\n  Au revoir !\n")

else:
    # Fallback : lancer le bot directement (stdin non-tty, pas de PORT)
    print("[NovaBot] Mode fallback – lancement direct du bot...")
    from config import LICHESS_TOKEN
    from bot.lichess_bot import LichessBot
    try:
        bot = LichessBot(LICHESS_TOKEN)
        bot.run()
    except KeyboardInterrupt:
        print("[NovaBot] Arrêt.")
    except Exception as e:
        print(f"[NovaBot] Erreur : {e}")
        raise
