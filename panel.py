# panel.py
# NovaBot – Panel de contrôle complet (Tkinter)
# Version prête à copier/coller. Place ce fichier à la racine du projet
# (même dossier que setup.py, config.py, engine/).

# --- BEGIN SHIM: forcer l'utilisation de engine/evaluate.py si présent ---
from pathlib import Path
import sys
import importlib.util
try:
    ROOT = Path(__file__).parent.resolve()
except Exception:
    ROOT = Path(".").resolve()

_eval_py = ROOT / "engine" / "evaluate.py"
if _eval_py.exists():
    try:
        spec = importlib.util.spec_from_file_location("engine.evaluate", str(_eval_py))
        if spec is not None:
            module = importlib.util.module_from_spec(spec)
            pkg_name = "engine"
            if pkg_name not in sys.modules:
                import types
                pkg = types.ModuleType(pkg_name)
                pkg.__path__ = [str(ROOT / "engine")]
                sys.modules[pkg_name] = pkg
            sys.modules["engine.evaluate"] = module
            spec.loader.exec_module(module)
            print("[Panel DEBUG] Loaded engine.evaluate from evaluate.py (shim).")
    except Exception as e:
        try:
            if "engine.evaluate" in sys.modules:
                del sys.modules["engine.evaluate"]
        except Exception:
            pass
        print(f"[Panel DEBUG] Shim load failed: {e}")
# --- END SHIM ---

import sys
from pathlib import Path
import subprocess
import threading
import time
import traceback
from queue import Queue, Empty
import tkinter as tk
from tkinter import scrolledtext, messagebox

# Ensure project root is in sys.path so imports like "engine.loader" work
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Try to import config values (fallbacks if missing)
try:
    from config import LICHESS_TOKEN, USE_NNUE
except Exception:
    LICHESS_TOKEN = ""
    USE_NNUE = False

# Robust import of engine.loader (may be None if import fails)
try:
    import engine.loader as engine_loader
except Exception as e:
    engine_loader = None
    print(f"[Panel DEBUG] Impossible d'importer engine.loader: {e}")

# Do not import bot.lichess_bot at module import time to avoid hiding import errors.
LichessBot = None

class NovaBotPanel:
    def __init__(self, root):
        self.root = root
        root.title("NovaBot – Panel de contrôle")

        # Threading / synchronization
        self.compile_lock = threading.Lock()
        self.bot_thread = None
        self.bot_stop_event = threading.Event()
        self.log_q = Queue()

        # Token
        tk.Label(root, text="Lichess Token:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.token_var = tk.StringVar(value=LICHESS_TOKEN or "")
        tk.Entry(root, textvariable=self.token_var, width=56).grid(row=0, column=1, columnspan=3, sticky="we", padx=6)

        # Buttons
        self.btn_compile = tk.Button(root, text="Compiler Cython", command=self.compile_cython)
        self.btn_compile.grid(row=1, column=0, padx=6, pady=6, sticky="we")

        self.btn_start = tk.Button(root, text="Démarrer bot", command=self.start_bot)
        self.btn_start.grid(row=1, column=1, padx=6, pady=6, sticky="we")

        self.btn_stop = tk.Button(root, text="Arrêter bot", command=self.stop_bot)
        self.btn_stop.grid(row=1, column=2, padx=6, pady=6, sticky="we")

        self.btn_test = tk.Button(root, text="Test Eval", command=self.test_eval)
        self.btn_test.grid(row=1, column=3, padx=6, pady=6, sticky="we")

        # Stats labels
        self.nps_var = tk.StringVar(value="NPS: -")
        self.ponder_var = tk.StringVar(value="Ponder: -")
        self.book_var = tk.StringVar(value="Book move: -")
        self.nnue_var = tk.StringVar(value=f"NNUE: {'ON' if USE_NNUE else 'OFF'}")

        tk.Label(root, textvariable=self.nps_var).grid(row=2, column=0, sticky="w", padx=6)
        tk.Label(root, textvariable=self.ponder_var).grid(row=2, column=1, sticky="w", padx=6)
        tk.Label(root, textvariable=self.book_var).grid(row=2, column=2, sticky="w", padx=6)
        tk.Label(root, textvariable=self.nnue_var).grid(row=2, column=3, sticky="w", padx=6)

        # Log box
        self.log_box = scrolledtext.ScrolledText(root, width=110, height=28, state="disabled", wrap="none")
        self.log_box.grid(row=3, column=0, columnspan=4, padx=6, pady=6, sticky="nsew")

        # Grid weight
        root.grid_rowconfigure(3, weight=1)
        root.grid_columnconfigure(3, weight=1)

        # Start periodic UI update
        self.root.after(200, self._periodic_update)

    # Optional: allow print redirection if you want
    def write(self, msg):
        self._log(msg)

    def flush(self):
        pass

    # -------------------------
    # Compilation (fix cwd)
    # -------------------------
    def compile_cython(self):
        """Compile les extensions Cython en exécutant setup.py depuis le dossier du projet.
        Recherche automatiquement setup.py en remontant jusqu'à 4 niveaux.
        """
        if self.compile_lock.locked():
            messagebox.showinfo("Compilation", "Compilation déjà en cours.")
            return

        def _find_setup(start_path, max_up=4):
            p = Path(start_path).resolve()
            for _ in range(max_up + 1):
                if (p / "setup.py").exists():
                    return p
                if p.parent == p:
                    break
                p = p.parent
            return None

        def _compile():
            with self.compile_lock:
                self._log("[Panel] Lancement compilation Cython...")
                start = ROOT
                project_root = _find_setup(start, max_up=4)
                if project_root is None:
                    self._log(f"[Panel] setup.py introuvable (recherché depuis {start}).")
                    messagebox.showerror("Erreur compilation", f"setup.py introuvable (recherché depuis {start}).")
                    return

                setup_path = project_root / "setup.py"
                cmd = [sys.executable, str(setup_path), "build_ext", "--inplace"]
                try:
                    proc = subprocess.run(
                        cmd,
                        cwd=str(project_root),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        check=False
                    )
                except Exception as e:
                    self._log(f"[Panel] Exception lors de l'appel subprocess: {e}")
                    messagebox.showerror("Erreur compilation", f"Exception: {e}")
                    return

                out = proc.stdout or ""
                for line in out.splitlines():
                    self._log(line)

                if proc.returncode == 0:
                    self._log("[Panel] Compilation terminée avec succès.")
                    messagebox.showinfo("Compilation", "Compilation Cython terminée.")
                else:
                    self._log(f"[Panel] Erreur compilation (code {proc.returncode}).")
                    messagebox.showerror("Erreur compilation", f"Code {proc.returncode}. Voir logs.")

        threading.Thread(target=_compile, daemon=True).start()

    # -------------------------
    # Bot control (import LichessBot lazily)
    # -------------------------
    def _run_bot(self, token, bot_cls):
        """Thread target: start engine and run the Lichess bot."""
        try:
            if engine_loader is None:
                self._log("[Panel] engine.loader introuvable. Impossible de démarrer le moteur.")
                return
            try:
                engine_loader.start_engine()
                self._log("[Panel] Engine démarré.")
            except Exception as e:
                self._log(f"[Panel] Erreur démarrage engine: {e}")

            if bot_cls is None:
                self._log("[Panel] LichessBot non disponible (import échoué).")
                return

            try:
                bot = bot_cls(token=token)
            except Exception as e:
                self._log(f"[Panel] Erreur initialisation LichessBot: {e}")
                self._log(traceback.format_exc())
                return

            try:
                # run should accept stop_event and stats_queue if implemented
                bot.run(stop_event=self.bot_stop_event, stats_queue=self.log_q)
            except TypeError:
                try:
                    bot.run()
                except Exception as e:
                    self._log(f"[Panel] Erreur lors de l'exécution du bot: {e}")
                    self._log(traceback.format_exc())
        except Exception as e:
            self._log(f"[Panel] Erreur bot: {e}")
            self._log(traceback.format_exc())

    def start_bot(self):
        if self.bot_thread and self.bot_thread.is_alive():
            messagebox.showinfo("Bot", "Le bot tourne déjà.")
            return
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("Token", "Token Lichess manquant.")
            return

        # Import LichessBot here and capture any import error to show full trace
        bot_cls = None
        try:
            # Import deferred to avoid import-time failures when panel imports module
            from bot.lichess_bot import LichessBot as _LB
            bot_cls = _LB
        except Exception as e:
            # Log full traceback in panel and show a messagebox with short info
            tb = traceback.format_exc()
            self._log(f"[Panel] Erreur import LichessBot: {e}")
            self._log(tb)
            messagebox.showerror("Import LichessBot", f"Erreur lors de l'import de bot.lichess_bot.\nVoir logs pour la trace complète.")
            return

        self.bot_stop_event.clear()
        self.bot_thread = threading.Thread(target=self._run_bot, args=(token, bot_cls), daemon=True)
        self.bot_thread.start()
        self._log("[Panel] Bot démarré.")

    def stop_bot(self):
        if not self.bot_thread:
            return
        self.bot_stop_event.set()
        try:
            if engine_loader is not None:
                engine_loader.stop_engine()
        except Exception:
            pass
        self._log("[Panel] Arrêt demandé…")

    # -------------------------
    # Test / utilities
    # -------------------------
    def test_eval(self):
        try:
            if engine_loader is None:
                self._log("[Panel] engine.loader introuvable.")
                return
            res = engine_loader.test_evaluate()
            self._log(f"[Panel] Test eval: {res}")
        except Exception as e:
            self._log(f"[Panel] Test eval erreur: {e}")
            self._log(traceback.format_exc())

    # -------------------------
    # Logging & UI update
    # -------------------------
    def _log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state="disabled")

    def _periodic_update(self):
        # Drain queue of messages from bot/engine
        try:
            while True:
                item = self.log_q.get_nowait()
                if isinstance(item, dict):
                    self._log(" | ".join(f"{k}={v}" for k, v in item.items()))
                else:
                    self._log(str(item))
        except Empty:
            pass

        # Update stats from engine_loader.get_stats() if available
        try:
            if engine_loader is not None:
                stats = engine_loader.get_stats()
                if stats:
                    self.nps_var.set(f"NPS: {stats.get('nps', '-')}")
                    self.ponder_var.set(f"Ponder: {stats.get('ponder', '-')}")
                    self.book_var.set(f"Book move: {stats.get('book_move', '-')}")
                    self.nnue_var.set(f"NNUE: {'ON' if stats.get('nnue', False) else 'OFF'}")
        except Exception:
            pass

        # Update every 500 ms
        self.root.after(500, self._periodic_update)


def main():
    root = tk.Tk()
    app = NovaBotPanel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
