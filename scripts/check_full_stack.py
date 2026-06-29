#!/usr/bin/env python3
import importlib
import json
import sys
import urllib.request


CHECKS = [
    ("FastAPI", "fastapi"),
    ("Pydantic", "pydantic"),
    ("PyMuPDF", "fitz"),
    ("trafilatura", "trafilatura"),
    ("GitPython", "git"),
    ("watchdog", "watchdog"),
    ("NumPy", "numpy"),
    ("Torch", "torch"),
    ("Transformers", "transformers"),
    ("sentence-transformers", "sentence_transformers"),
    ("FAISS", "faiss"),
    ("tree-sitter", "tree_sitter"),
    ("tree-sitter Python", "tree_sitter_python"),
    ("tree-sitter JavaScript", "tree_sitter_javascript"),
    ("tree-sitter TypeScript", "tree_sitter_typescript"),
    ("tree-sitter C++", "tree_sitter_cpp"),
    ("tree-sitter C", "tree_sitter_c"),
]


def check_import(label, module):
    try:
        imported = importlib.import_module(module)
        version = getattr(imported, "__version__", "installed")
        return {"label": label, "ok": True, "version": str(version)}
    except Exception as exc:
        return {"label": label, "ok": False, "error": str(exc)}


def check_ollama():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
        models = [item.get("name", "") for item in data.get("models", [])]
        return {"label": "Ollama", "ok": True, "models": models}
    except Exception as exc:
        return {"label": "Ollama", "ok": False, "error": f"{exc}. Start Ollama with: ollama serve"}


def main():
    results = [check_import(label, module) for label, module in CHECKS]
    results.append(check_ollama())
    print("\nReadSync full-stack dependency check")
    print("=" * 40)
    for item in results:
        if item["ok"]:
            suffix = item.get("version") or ", ".join(item.get("models", []))
            print(f"✓ {item['label']}: {suffix}")
        else:
            print(f"✗ {item['label']}: {item['error']}")
    failed = [item for item in results if not item["ok"] and item["label"] != "Ollama"]
    if failed:
        print("\nMissing required Python packages. Re-run:")
        print("  source .venv/bin/activate")
        print("  python -m pip install -r backend/requirements.txt")
        sys.exit(1)
    if not results[-1]["ok"]:
        print("\nOllama is optional for opening the app, but required for true LLM extraction.")
        print("Run:")
        print("  ollama pull llama3.2")
        print("  ollama serve")
    print("\nFull Python ML stack is installed.")


if __name__ == "__main__":
    main()
