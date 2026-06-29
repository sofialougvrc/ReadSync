# ReadSync

ReadSync is a private local research-to-code mapping system. It ingests papers and technical articles, extracts implementation-facing concepts with a local Ollama model, indexes local source repositories, and proposes paper-to-code links for review.

## Full Python 3.12 runtime

This version uses the full stack again:

- FastAPI
- Pydantic validation
- PyMuPDF PDF text extraction
- trafilatura article cleaning
- Ollama structured extraction
- Torch
- Transformers
- sentence-transformers embeddings
- FAISS candidate retrieval
- Tree-sitter parsing for JS/TS/C/C++ with fallback chunking
- Python AST parsing
- GitPython commit metadata
- SQLite persistence
- React + Vite + D3 frontend

## Run

```bash
cd /Users/sofiacardenasgarcia/Documents/Codex/ReadSync
./setup.sh
```

Open:

```text
http://127.0.0.1:8000
```

## Load sample data

In another terminal, after setup has installed the virtual environment:

```bash
cd /Users/sofiacardenasgarcia/Documents/Codex/ReadSync
source .venv/bin/activate
python scripts/sample_data.py
```

Refresh the browser. You will see a sample Transformer paper, a sample repository, indexed code chunks, graph nodes, and pending matches.

## Ollama

ReadSync expects Ollama locally:

```bash
ollama pull llama3.2
ollama serve
```

The model and endpoint are configurable in `.env`.

## Enable sentence-transformer embeddings

The Python package is installed by setup. The actual embedding model is downloaded separately the first time you want full neural embeddings:

```bash
cd /Users/sofiacardenasgarcia/Documents/Codex/ReadSync
source .venv/bin/activate
python scripts/download_embedding_model.py
```

After this is cached, ReadSync uses `sentence-transformers/all-MiniLM-L6-v2` for indexing and matching. If the model is not cached, ReadSync falls back to deterministic local embeddings instead of freezing while trying to reach Hugging Face.

## Verify the full ML stack

After setup:

```bash
cd /Users/sofiacardenasgarcia/Documents/Codex/ReadSync
source .venv/bin/activate
python scripts/check_full_stack.py
```

This checks FastAPI, Pydantic, PyMuPDF, Torch, Transformers, sentence-transformers, FAISS, Tree-sitter grammars, and Ollama connectivity.
