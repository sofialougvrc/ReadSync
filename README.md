# ReadSync
ReadSync is a local research-to-code mapping system that connects academic reading to real implementation work. It ingests papers, arXiv links, DOI records, BibTeX sources, and technical articles, extracts implementation-facing concepts with a local LLM, indexes source repositories at function and class granularity, and proposes links between research ideas and code for review.

The goal is not simply document search or repository search in isolation. ReadSync is designed to make the gap between what has been read and what has actually been built inspectable inside a single workspace.

## What it does

- ingests PDFs, technical articles, metadata records, and citation sources
- extracts concepts, algorithms, metrics, datasets, limitations, and citations through typed structured output
- indexes local repositories with Tree-sitter and Python AST parsing
- preserves function and class structure rather than flattening code into arbitrary text windows
- computes embeddings for research concepts and code chunks
- retrieves candidate matches with FAISS
- routes candidates through an Ollama-based LLM judge
- records whether a concept appears implemented, partially present, or missing
- surfaces results in a React + D3 review workspace with graph views, code exploration, notes, and review state

## Stack

- FastAPI
- Pydantic
- PyMuPDF
- trafilatura
- Ollama
- PyTorch
- Hugging Face Transformers
- sentence-transformers
- FAISS
- Tree-sitter
- Python AST parsing
- GitPython
- SQLite
- React
- Vite
- D3.js

## Run locally

```bash
cd /Users/sofiacardenasgarcia/Documents/Codex/ReadSync
./setup.sh
```

Open:

```text
http://127.0.0.1:8000
```

## Sample data

To load a small working example after setup:

```bash
cd /Users/sofiacardenasgarcia/Documents/Codex/ReadSync
source .venv/bin/activate
python scripts/sample_data.py
```

This seeds the workspace with a sample paper, a sample repository, indexed code chunks, and pending review links so the full interface can be explored immediately.

## Ollama

ReadSync uses Ollama locally for structured extraction and match review:

```bash
ollama pull llama3.2
ollama serve
```

The endpoint and model are configurable in `.env`.

## Embeddings

The repository installs the Python dependencies during setup, but the neural embedding model is downloaded separately when full sentence-transformer retrieval is desired:

```bash
cd /Users/sofiacardenasgarcia/Documents/Codex/ReadSync
source .venv/bin/activate
python scripts/download_embedding_model.py
```

Once cached, ReadSync uses `sentence-transformers/all-MiniLM-L6-v2` for concept and code retrieval. If the model is unavailable locally, the system falls back to deterministic local embeddings instead of blocking on a remote download.

## Verification

To verify the local runtime and ML stack:

```bash
cd /Users/sofiacardenasgarcia/Documents/Codex/ReadSync
source .venv/bin/activate
python scripts/check_full_stack.py
```

This checks:

- FastAPI
- Pydantic
- PyMuPDF
- PyTorch
- Transformers
- sentence-transformers
- FAISS
- Tree-sitter grammars
- Ollama connectivity

## Notes

- ReadSync is designed as a local-first system: papers, code chunks, embeddings, and review decisions are stored on the machine in SQLite.
- Repository indexing preserves structural context such as signatures, imports, calls, docstrings, file paths, and line numbers.
- The system is intended for implementation traceability and research review, not for automated code generation.
