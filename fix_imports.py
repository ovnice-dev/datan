# fix_imports.py
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
pattern = "from engine.evaluate import evaluate"
replacement = "from engine.evaluate_wrapper import evaluate"

files = list(ROOT.rglob("*.py"))
patched = []
for f in files:
    if f.name == "fix_imports.py":
        continue
    text = f.read_text(encoding="utf-8")
    if pattern in text:
        new_text = text.replace(pattern, replacement)
        f.write_text(new_text, encoding="utf-8")
        patched.append(str(f.relative_to(ROOT)))

print("Fichiers patchés :", patched)
if not patched:
    print("Aucun import à remplacer trouvé.")
