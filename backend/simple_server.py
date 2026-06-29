#!/usr/bin/env python3
"""
ReadSync Python 3.14 server.

This runtime intentionally uses only the Python standard library so ReadSync can
run on machines where FastAPI/Pydantic wheels are not available yet.
"""

from __future__ import annotations

import ast
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "readsync_py314.db"
FRONTEND_DIST = ROOT / "frontend" / "dist"
SUPPORTED_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".c", ".cpp", ".h", ".hpp"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, fallback: Any = None) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS papers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              authors_json TEXT NOT NULL DEFAULT '[]',
              abstract TEXT NOT NULL DEFAULT '',
              year INTEGER,
              categories_json TEXT NOT NULL DEFAULT '[]',
              arxiv_id TEXT,
              doi TEXT,
              source_url TEXT,
              source_type TEXT NOT NULL DEFAULT 'manual',
              raw_text TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'ingested',
              extraction_error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_arxiv ON papers(arxiv_id) WHERE arxiv_id IS NOT NULL AND arxiv_id != '';
            CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi) WHERE doi IS NOT NULL AND doi != '';
            CREATE TABLE IF NOT EXISTS concepts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              description TEXT NOT NULL,
              type_tag TEXT NOT NULL,
              confidence REAL NOT NULL DEFAULT 0.5
            );
            CREATE TABLE IF NOT EXISTS algorithms (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              description TEXT NOT NULL,
              pseudocode TEXT NOT NULL DEFAULT '',
              confidence REAL NOT NULL DEFAULT 0.5
            );
            CREATE TABLE IF NOT EXISTS code_patterns (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              description TEXT NOT NULL,
              language TEXT NOT NULL DEFAULT '',
              confidence REAL NOT NULL DEFAULT 0.5
            );
            CREATE TABLE IF NOT EXISTS paper_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,
              label TEXT NOT NULL,
              detail TEXT NOT NULL DEFAULT '',
              confidence REAL NOT NULL DEFAULT 0.5
            );
            CREATE TABLE IF NOT EXISTS repositories (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              path TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL DEFAULT 'pending',
              last_indexed_at TEXT,
              chunk_count INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS code_chunks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repo_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
              file_path TEXT NOT NULL,
              language TEXT NOT NULL,
              module_path TEXT NOT NULL,
              class_name TEXT NOT NULL DEFAULT '',
              signature TEXT NOT NULL,
              docstring TEXT NOT NULL DEFAULT '',
              body TEXT NOT NULL DEFAULT '',
              imports_json TEXT NOT NULL DEFAULT '[]',
              calls_json TEXT NOT NULL DEFAULT '[]',
              start_line INTEGER NOT NULL DEFAULT 1,
              end_line INTEGER NOT NULL DEFAULT 1,
              last_commit TEXT NOT NULL DEFAULT '',
              last_modified TEXT NOT NULL DEFAULT '',
              embedding_json TEXT NOT NULL DEFAULT '[]',
              structural_text TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS matches (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
              concept_id INTEGER REFERENCES concepts(id) ON DELETE CASCADE,
              algorithm_id INTEGER REFERENCES algorithms(id) ON DELETE CASCADE,
              chunk_id INTEGER NOT NULL REFERENCES code_chunks(id) ON DELETE CASCADE,
              track TEXT NOT NULL,
              classification TEXT NOT NULL,
              reason TEXT NOT NULL,
              confidence REAL NOT NULL,
              semantic_score REAL NOT NULL DEFAULT 0,
              structural_score REAL NOT NULL DEFAULT 0,
              review_state TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              paper_id INTEGER REFERENCES papers(id) ON DELETE CASCADE,
              chunk_id INTEGER REFERENCES code_chunks(id) ON DELETE CASCADE,
              body TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL,
              title TEXT NOT NULL,
              detail TEXT NOT NULL DEFAULT '',
              ref_type TEXT NOT NULL DEFAULT '',
              ref_id INTEGER,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS errors (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              stage TEXT NOT NULL,
              message TEXT NOT NULL,
              payload_json TEXT NOT NULL DEFAULT '{}',
              paper_id INTEGER,
              created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            ("ollama_endpoint", "disabled-python314-stdlib-runtime", now()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            ("ollama_model", "heuristic-local-extractor", now()),
        )


def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def row(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        found = conn.execute(query, params).fetchone()
        return dict(found) if found else None


def activity(kind: str, title: str, detail: str = "", ref_type: str = "", ref_id: int | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO activity(kind, title, detail, ref_type, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, title, detail, ref_type, ref_id, now()),
        )


def error_log(stage: str, message: str, payload: Any = None, paper_id: int | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO errors(stage, message, payload_json, paper_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (stage, str(message)[:4000], dumps(payload or {}), paper_id, now()),
        )


def parse_json_columns(item: dict[str, Any]) -> dict[str, Any]:
    parsed = dict(item)
    for key in list(parsed.keys()):
        if key.endswith("_json"):
            parsed[key[:-5]] = loads(parsed.pop(key), [])
    return parsed


def text_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", (text or "").lower())


def fingerprint(text: str) -> set[str]:
    stop = {"the", "and", "for", "with", "from", "this", "that", "into", "using", "return", "class", "function", "const", "def"}
    return {token for token in text_tokens(text) if token not in stop}


def hash_embedding(text: str, dimension: int = 192) -> list[float]:
    vector = [0.0] * dimension
    tokens = text_tokens(text) or ["empty"]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % dimension
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[idx] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 6) for value in vector]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(size))
    na = math.sqrt(sum(a[i] * a[i] for i in range(size))) or 1.0
    nb = math.sqrt(sum(b[i] * b[i] for i in range(size))) or 1.0
    return max(0.0, min(1.0, dot / (na * nb)))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def arxiv_id(value: str) -> str:
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([A-Za-z0-9.\-]+)", value)
    if match:
        return match.group(1).replace(".pdf", "")
    match = re.search(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b", value)
    return match.group(0) if match else ""


def dedupe(title: str, authors: list[str], arxiv: str = "", doi: str = "") -> int | None:
    author_key = " ".join(authors[:2]).lower()
    key = title_key(title)
    for paper in rows("SELECT * FROM papers"):
        if arxiv and paper.get("arxiv_id") == arxiv:
            return int(paper["id"])
        if doi and paper.get("doi") == doi:
            return int(paper["id"])
        if key and title_key(paper["title"]) == key:
            row_authors = " ".join(loads(paper.get("authors_json"), [])[:2]).lower()
            if not author_key or author_key[:14] in row_authors:
                return int(paper["id"])
    return None


def store_paper(meta: dict[str, Any], raw_text: str, source_type: str, source_url: str = "") -> int:
    title = meta.get("title") or "Untitled paper"
    authors = meta.get("authors") or []
    existing = dedupe(title, authors, meta.get("arxiv_id", ""), meta.get("doi", ""))
    if existing:
        with connect() as conn:
            conn.execute("UPDATE papers SET raw_text = COALESCE(NULLIF(?, ''), raw_text), updated_at = ? WHERE id = ?", (raw_text, now(), existing))
        activity("dedupe", "Existing paper reused", title, "paper", existing)
        return existing
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO papers(title, authors_json, abstract, year, categories_json, arxiv_id, doi, source_url, source_type, raw_text, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ingested', ?, ?)
            """,
            (
                title,
                dumps(authors),
                meta.get("abstract", ""),
                meta.get("year"),
                dumps(meta.get("categories") or []),
                meta.get("arxiv_id", ""),
                meta.get("doi", ""),
                source_url,
                source_type,
                raw_text or "",
                now(),
                now(),
            ),
        )
        paper_id = int(cur.lastrowid)
    activity("ingest", "Paper ingested", title, "paper", paper_id)
    return paper_id


def fetch_arxiv(value: str) -> int:
    aid = arxiv_id(value)
    if not aid:
        raise ValueError("No arXiv ID found.")
    url = f"http://export.arxiv.org/api/query?id_list={urllib.parse.quote(aid)}"
    with urllib.request.urlopen(url, timeout=25) as response:
        root = ET.fromstring(response.read())
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        raise ValueError(f"arXiv record not found: {aid}")
    title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
    abstract = " ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split())
    published = entry.findtext("a:published", default="", namespaces=ns) or ""
    authors = [node.findtext("a:name", default="", namespaces=ns) for node in entry.findall("a:author", ns)]
    categories = [node.attrib.get("term", "") for node in entry.findall("a:category", ns)]
    return store_paper(
        {"title": title, "authors": authors, "abstract": abstract, "year": int(published[:4]) if published[:4].isdigit() else None, "categories": categories, "arxiv_id": aid},
        abstract,
        "arxiv",
        value,
    )


def ingest_article_url(url: str) -> int:
    with urllib.request.urlopen(url, timeout=25) as response:
        body = response.read().decode("utf-8", errors="ignore")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else url
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))).strip()
    return store_paper({"title": title, "authors": [], "abstract": text[:1200]}, text, "article", url)


def ingest_doi(doi: str) -> int:
    clean = doi.strip().replace("https://doi.org/", "")
    url = f"https://api.crossref.org/works/{urllib.parse.quote(clean)}"
    request = urllib.request.Request(url, headers={"User-Agent": "ReadSync local Python 3.14"})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = json.loads(response.read().decode("utf-8"))
    msg = data.get("message", {})
    title = (msg.get("title") or [clean])[0]
    authors = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in msg.get("author", [])]
    abstract = re.sub(r"<[^>]+>", " ", msg.get("abstract", ""))
    year = None
    try:
        year = msg.get("published-print", msg.get("published-online", {})).get("date-parts", [[None]])[0][0]
    except Exception:
        pass
    return store_paper({"title": title, "authors": authors, "year": year, "doi": clean, "abstract": abstract}, abstract or title, "doi", f"https://doi.org/{clean}")


def ingest_bibtex(text: str) -> list[int]:
    ids: list[int] = []
    entries = re.split(r"(?=@\w+\s*\{)", text)
    for entry in entries:
        if not entry.strip():
            continue
        fields = {key.lower(): value.strip() for key, value in re.findall(r"(\w+)\s*=\s*[{'\"]([^{}'\"]+)", entry, re.S)}
        title = fields.get("title", "Untitled BibTeX entry")
        authors = [name.strip() for name in fields.get("author", "").split(" and ") if name.strip()]
        year = int(fields["year"]) if fields.get("year", "").isdigit() else None
        ids.append(store_paper({"title": title, "authors": authors, "year": year, "doi": fields.get("doi", ""), "abstract": fields.get("abstract", "")}, entry, "bibtex", fields.get("url", "")))
    return ids


def ingest_pdf_placeholder(filename: str, data: bytes) -> int:
    digest = hashlib.sha256(data).hexdigest()[:12]
    text = f"PDF uploaded locally: {filename}. Python 3.14 stdlib runtime stored metadata and file hash. Install the optional PyMuPDF runtime later for full PDF text extraction."
    return store_paper({"title": Path(filename).stem, "authors": [], "abstract": text, "doi": f"local-pdf-{digest}"}, text, "pdf", filename)


def heuristic_extract(title: str, text: str) -> dict[str, Any]:
    lowered = (title + "\n" + text).lower()
    specs = [
        ("Attention Mechanism", "architecture_pattern", ["attention", "query", "key", "value", "transformer"]),
        ("Embedding Search", "systems_design", ["embedding", "vector", "similarity", "retrieval"]),
        ("Gradient-Based Optimization", "training_technique", ["gradient", "loss", "optimizer", "descent"]),
        ("Graph Algorithm", "algorithm", ["graph", "node", "edge", "path", "traversal"]),
        ("Evaluation Protocol", "evaluation", ["accuracy", "precision", "recall", "f1", "benchmark", "metric"]),
        ("Data Pipeline", "systems_design", ["dataset", "preprocess", "pipeline", "feature"]),
    ]
    concepts = []
    for name, tag, needles in specs:
        hits = [needle for needle in needles if needle in lowered]
        if hits:
            concepts.append({
                "name": name,
                "type_tag": tag,
                "description": f"{name} is relevant to this paper because ReadSync found implementation-facing language around {', '.join(hits)}.",
                "confidence": min(0.94, 0.48 + len(hits) * 0.1),
            })
    if not concepts:
        concepts.append({"name": "Core Method", "type_tag": "other", "description": f"ReadSync extracted a general implementation concept from '{title}'.", "confidence": 0.52})
    algorithms = []
    if re.search(r"\b(algorithm|procedure|training loop|pseudocode|iterate|optimi[sz]e)\b", lowered):
        algorithms.append({
            "name": "Paper Procedure",
            "description": "The paper contains procedural language that can be mapped against code.",
            "pseudocode": "load inputs\ninitialize method\niterate over examples\ncompute objective\nreturn output",
            "confidence": 0.58,
        })
    metrics = sorted(set(re.findall(r"\b(accuracy|precision|recall|f1|auc|roc|bleu|rouge|perplexity)\b", lowered)))[:12]
    citations = re.findall(r"\b[A-Z][A-Za-z-]+ et al\.,? \d{4}\b", text)[:16]
    return {"core_contribution": (text.strip()[:900] or title), "concepts": concepts, "algorithms": algorithms, "code_patterns": [], "datasets": [], "evaluation_metrics": metrics, "stated_limitations": [], "citations": citations}


def extract_paper(paper_id: int) -> dict[str, Any]:
    paper = row("SELECT * FROM papers WHERE id = ?", (paper_id,))
    if not paper:
        raise ValueError("Paper not found.")
    extraction = heuristic_extract(paper["title"], paper.get("raw_text") or paper.get("abstract") or "")
    with connect() as conn:
        for table in ("concepts", "algorithms", "code_patterns", "paper_items"):
            conn.execute(f"DELETE FROM {table} WHERE paper_id = ?", (paper_id,))
        for concept in extraction["concepts"]:
            conn.execute("INSERT INTO concepts(paper_id, name, description, type_tag, confidence) VALUES (?, ?, ?, ?, ?)", (paper_id, concept["name"], concept["description"], concept["type_tag"], concept["confidence"]))
        for algorithm in extraction["algorithms"]:
            conn.execute("INSERT INTO algorithms(paper_id, name, description, pseudocode, confidence) VALUES (?, ?, ?, ?, ?)", (paper_id, algorithm["name"], algorithm["description"], algorithm["pseudocode"], algorithm["confidence"]))
        for kind in ("datasets", "evaluation_metrics", "stated_limitations", "citations"):
            for value in extraction[kind]:
                conn.execute("INSERT INTO paper_items(paper_id, kind, label, detail, confidence) VALUES (?, ?, ?, '', .72)", (paper_id, kind[:-1], value))
        conn.execute("UPDATE papers SET status = 'extracted', abstract = COALESCE(NULLIF(abstract, ''), ?), updated_at = ? WHERE id = ?", (extraction["core_contribution"], now(), paper_id))
    activity("extract", "Structured extraction complete", paper["title"], "paper", paper_id)
    return extraction


def language_for(path: Path) -> str:
    return {".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp"}.get(path.suffix.lower(), "text")


def python_chunks(path: Path, source: str) -> list[dict[str, Any]]:
    tree = ast.parse(source)
    lines = source.splitlines()
    imports = [ast.get_source_segment(source, node) or "" for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    chunks = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            signature = f"class {node.name}" if isinstance(node, ast.ClassDef) else f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}({', '.join(arg.arg for arg in node.args.args)})"
            body = "\n".join(lines[start - 1:end])
            calls = sorted({n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)})
            chunks.append({"signature": signature, "class_name": "", "docstring": ast.get_docstring(node) or "", "body": body, "imports": imports, "calls": calls, "start_line": start, "end_line": end})
    return chunks


def regex_chunks(path: Path, source: str) -> list[dict[str, Any]]:
    lines = source.splitlines()
    pattern = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+[\w$]+|const\s+[\w$]+\s*=\s*(?:async\s*)?\(|class\s+[\w$]+|[\w:<>&*\s]+\s+[\w:~]+\s*\([^;]*\)\s*\{)")
    starts = [idx + 1 for idx, line in enumerate(lines) if pattern.search(line)] or ([1] if source.strip() else [])
    chunks = []
    for idx, start in enumerate(starts[:240]):
        end = starts[idx + 1] - 1 if idx + 1 < len(starts) else min(len(lines), start + 120)
        body = "\n".join(lines[start - 1:end])
        signature = lines[start - 1].strip()[:220] if lines else path.name
        imports = [line.strip() for line in lines if line.strip().startswith(("import ", "#include", "const "))][:30]
        calls = sorted(set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body)))[:60]
        chunks.append({"signature": signature, "class_name": "", "docstring": "", "body": body, "imports": imports, "calls": calls, "start_line": start, "end_line": end})
    return chunks


def add_repo(path: str) -> int:
    repo_path = Path(path).expanduser().resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise ValueError("Repository path does not exist or is not a directory.")
    with connect() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO repositories(name, path, status, created_at) VALUES (?, ?, 'pending', ?)", (repo_path.name, str(repo_path), now()))
        repo_id = cur.lastrowid or conn.execute("SELECT id FROM repositories WHERE path = ?", (str(repo_path),)).fetchone()["id"]
    activity("repo", "Repository added", str(repo_path), "repo", int(repo_id))
    return int(repo_id)


def index_repo(repo_id: int) -> dict[str, Any]:
    repo = row("SELECT * FROM repositories WHERE id = ?", (repo_id,))
    if not repo:
        raise ValueError("Repository not found.")
    repo_path = Path(repo["path"])
    count = 0
    with connect() as conn:
        conn.execute("UPDATE repositories SET status = 'indexing' WHERE id = ?", (repo_id,))
        conn.execute("DELETE FROM code_chunks WHERE repo_id = ?", (repo_id,))
        for root, dirs, files in os.walk(repo_path):
            parts = set(Path(root).parts)
            if {".git", "node_modules", ".venv", "dist"} & parts:
                continue
            for filename in files:
                path = Path(root) / filename
                if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                try:
                    source = path.read_text(errors="ignore")
                    lang = language_for(path)
                    chunks = python_chunks(path, source) if lang == "python" else regex_chunks(path, source)
                except Exception:
                    chunks = regex_chunks(path, path.read_text(errors="ignore"))
                    lang = language_for(path)
                modified = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
                for chunk in chunks:
                    module_path = str(path.relative_to(repo_path)) if str(path).startswith(str(repo_path)) else str(path)
                    structural = " ".join(fingerprint(" ".join([chunk["signature"], " ".join(chunk["imports"]), " ".join(chunk["calls"])])))
                    embedding = hash_embedding("\n".join([chunk["signature"], chunk["docstring"], chunk["body"][:9000]]))
                    conn.execute(
                        """
                        INSERT INTO code_chunks(repo_id, file_path, language, module_path, class_name, signature, docstring, body, imports_json, calls_json, start_line, end_line, last_modified, embedding_json, structural_text, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (repo_id, str(path), lang, module_path, chunk["class_name"], chunk["signature"], chunk["docstring"], chunk["body"], dumps(chunk["imports"]), dumps(chunk["calls"]), chunk["start_line"], chunk["end_line"], modified, dumps(embedding), structural, now()),
                    )
                    count += 1
        conn.execute("UPDATE repositories SET status = 'indexed', last_indexed_at = ?, chunk_count = ? WHERE id = ?", (now(), count, repo_id))
    activity("index", "Repository indexed", f"{repo_path} · {count} chunks", "repo", repo_id)
    return {"repo_id": repo_id, "chunks": count}


def run_matching(paper_id: int | None = None) -> dict[str, Any]:
    concept_query = "SELECT *, 'concept' as idea_kind FROM concepts"
    params: tuple[Any, ...] = ()
    if paper_id:
        concept_query += " WHERE paper_id = ?"
        params = (paper_id,)
    ideas = rows(concept_query, params)
    algos = rows("SELECT *, 'algorithm' as idea_kind FROM algorithms" + (" WHERE paper_id = ?" if paper_id else ""), params)
    ideas.extend(algos)
    chunks = rows("SELECT c.*, r.name as repo_name FROM code_chunks c JOIN repositories r ON r.id = c.repo_id")
    created = 0
    with connect() as conn:
        if paper_id:
            conn.execute("DELETE FROM matches WHERE paper_id = ? AND review_state = 'pending'", (paper_id,))
        for idea in ideas:
            idea_text = "\n".join([idea.get("name", ""), idea.get("description", ""), idea.get("pseudocode", "")])
            idea_embedding = hash_embedding(idea_text)
            idea_words = fingerprint(idea_text)
            scored = []
            for chunk in chunks:
                semantic = cosine(idea_embedding, loads(chunk.get("embedding_json"), []))
                structural = jaccard(idea_words, fingerprint(chunk.get("structural_text") or chunk.get("body") or ""))
                combined = semantic * 0.72 + structural * 0.28
                if combined > 0.1:
                    scored.append((combined, semantic, structural, chunk))
            for combined, semantic, structural, chunk in sorted(scored, key=lambda item: item[0], reverse=True)[:8]:
                classification = "already_implemented" if combined >= 0.55 else "unapplied"
                reason = "The code vocabulary and function body overlap strongly with the extracted paper idea." if classification == "already_implemented" else "The code is semantically nearby, but ReadSync cannot confirm a complete implementation yet."
                conn.execute(
                    """
                    INSERT INTO matches(paper_id, concept_id, algorithm_id, chunk_id, track, classification, reason, confidence, semantic_score, structural_score, review_state, created_at)
                    VALUES (?, ?, ?, ?, 'semantic+structural', ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (idea["paper_id"], idea["id"] if idea["idea_kind"] == "concept" else None, idea["id"] if idea["idea_kind"] == "algorithm" else None, chunk["id"], classification, reason, max(0.35, min(0.95, combined)), semantic, structural, now()),
                )
                created += 1
    activity("match", "Matching run complete", f"{created} candidate links created", "paper", paper_id)
    return {"matches_created": created, "ideas": len(ideas), "chunks": len(chunks)}


def load_sample_data() -> dict[str, Any]:
    sample_repo = ROOT / "sample_repo"
    sample_repo.mkdir(exist_ok=True)
    (sample_repo / "attention.py").write_text(
        """import math

def scaled_dot_product_attention(query, key, value):
    \"\"\"Compute transformer-style attention weights and aggregate values.\"\"\"
    scores = query @ key.T / math.sqrt(key.shape[-1])
    weights = softmax(scores)
    return weights @ value

def softmax(values):
    exp_values = [math.exp(v) for v in values]
    total = sum(exp_values)
    return [v / total for v in exp_values]
""",
        encoding="utf-8",
    )
    paper_id = store_paper(
        {
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
            "year": 2017,
            "arxiv_id": "1706.03762",
            "categories": ["cs.CL", "cs.LG"],
            "abstract": "The Transformer replaces recurrent sequence modeling with attention mechanisms.",
        },
        "The Transformer relies entirely on attention mechanisms. Scaled dot-product attention uses queries, keys, and values. We evaluate machine translation with BLEU.",
        "sample",
        "https://arxiv.org/abs/1706.03762",
    )
    extract_paper(paper_id)
    repo_id = add_repo(str(sample_repo))
    index_repo(repo_id)
    match_stats = run_matching(paper_id)
    return {"paper_id": paper_id, "repo_id": repo_id, **match_stats}


def parse_multipart(content_type: str, body: bytes) -> tuple[str, bytes]:
    boundary_match = re.search(r"boundary=([^;]+)", content_type)
    if not boundary_match:
        raise ValueError("Missing multipart boundary.")
    boundary = ("--" + boundary_match.group(1).strip('"')).encode()
    for part in body.split(boundary):
        if b"Content-Disposition" not in part:
            continue
        header, _, data = part.partition(b"\r\n\r\n")
        filename_match = re.search(rb'filename="([^"]+)"', header)
        filename = filename_match.group(1).decode("utf-8", errors="ignore") if filename_match else "upload.pdf"
        return filename, data.rstrip(b"\r\n--")
    raise ValueError("No file part found.")


def route_get(path: str, query: dict[str, list[str]]) -> Any:
    if path == "/api/health":
        return {"ok": True, "runtime": "Python 3.14 standard-library backend", "database": str(DB_PATH)}
    if path == "/api/dashboard":
        stats = {
            "papers": row("SELECT COUNT(*) as count FROM papers")["count"],
            "concepts": row("SELECT COUNT(*) as count FROM concepts")["count"],
            "repos": row("SELECT COUNT(*) as count FROM repositories")["count"],
            "chunks": row("SELECT COUNT(*) as count FROM code_chunks")["count"],
            "pending": row("SELECT COUNT(*) as count FROM matches WHERE review_state = 'pending'")["count"],
            "accepted": row("SELECT COUNT(*) as count FROM matches WHERE review_state = 'accepted'")["count"],
        }
        pending = rows(
            """
            SELECT m.*, p.title as paper_title, COALESCE(c.name, a.name) as idea_name, ch.module_path, ch.signature, r.name as repo_name
            FROM matches m JOIN papers p ON p.id = m.paper_id
            LEFT JOIN concepts c ON c.id = m.concept_id
            LEFT JOIN algorithms a ON a.id = m.algorithm_id
            JOIN code_chunks ch ON ch.id = m.chunk_id
            JOIN repositories r ON r.id = ch.repo_id
            WHERE m.review_state = 'pending'
            ORDER BY m.confidence DESC LIMIT 12
            """
        )
        return {"stats": stats, "recent": rows("SELECT * FROM activity ORDER BY id DESC LIMIT 20"), "pending": pending}
    if path == "/api/papers":
        q = query.get("q", [""])[0]
        if q:
            data = rows("SELECT id, title, authors_json, abstract, year, categories_json, arxiv_id, doi, source_type, status, created_at, updated_at FROM papers WHERE title LIKE ? OR abstract LIKE ? ORDER BY updated_at DESC", (f"%{q}%", f"%{q}%"))
        else:
            data = rows("SELECT id, title, authors_json, abstract, year, categories_json, arxiv_id, doi, source_type, status, created_at, updated_at FROM papers ORDER BY updated_at DESC")
        return [parse_json_columns(item) for item in data]
    paper_match = re.fullmatch(r"/api/papers/(\d+)", path)
    if paper_match:
        paper_id = int(paper_match.group(1))
        paper = row("SELECT * FROM papers WHERE id = ?", (paper_id,))
        if not paper:
            raise KeyError("Paper not found.")
        detail = parse_json_columns(paper)
        detail["concepts"] = rows("SELECT * FROM concepts WHERE paper_id = ? ORDER BY confidence DESC", (paper_id,))
        detail["algorithms"] = rows("SELECT * FROM algorithms WHERE paper_id = ? ORDER BY confidence DESC", (paper_id,))
        detail["code_patterns"] = rows("SELECT * FROM code_patterns WHERE paper_id = ? ORDER BY confidence DESC", (paper_id,))
        detail["items"] = rows("SELECT * FROM paper_items WHERE paper_id = ? ORDER BY kind, label", (paper_id,))
        detail["notes"] = rows("SELECT * FROM notes WHERE paper_id = ? ORDER BY updated_at DESC", (paper_id,))
        detail["matches"] = rows(
            """
            SELECT m.*, COALESCE(c.name, a.name) as idea_name, ch.module_path, ch.signature, ch.start_line, ch.end_line, ch.body, r.name as repo_name
            FROM matches m LEFT JOIN concepts c ON c.id = m.concept_id
            LEFT JOIN algorithms a ON a.id = m.algorithm_id
            JOIN code_chunks ch ON ch.id = m.chunk_id
            JOIN repositories r ON r.id = ch.repo_id
            WHERE m.paper_id = ? ORDER BY m.confidence DESC
            """,
            (paper_id,),
        )
        return detail
    if path == "/api/repos":
        return rows("SELECT * FROM repositories ORDER BY created_at DESC")
    if path == "/api/chunks":
        q = query.get("q", [""])[0]
        if q:
            return rows("SELECT c.*, r.name as repo_name FROM code_chunks c JOIN repositories r ON r.id = c.repo_id WHERE c.module_path LIKE ? OR c.signature LIKE ? OR c.body LIKE ? ORDER BY c.module_path LIMIT 500", (f"%{q}%", f"%{q}%", f"%{q}%"))
        return rows("SELECT c.*, r.name as repo_name FROM code_chunks c JOIN repositories r ON r.id = c.repo_id ORDER BY c.module_path LIMIT 500")
    if path == "/api/matches":
        state = query.get("review_state", [""])[0]
        clause = "WHERE m.review_state = ?" if state else ""
        params = (state,) if state else ()
        return rows(
            f"""
            SELECT m.*, p.title as paper_title, COALESCE(c.name, a.name) as idea_name, ch.module_path, ch.signature, ch.body, r.name as repo_name
            FROM matches m JOIN papers p ON p.id = m.paper_id
            LEFT JOIN concepts c ON c.id = m.concept_id
            LEFT JOIN algorithms a ON a.id = m.algorithm_id
            JOIN code_chunks ch ON ch.id = m.chunk_id
            JOIN repositories r ON r.id = ch.repo_id
            {clause} ORDER BY m.created_at DESC LIMIT 500
            """,
            params,
        )
    if path == "/api/graph":
        confidence = float(query.get("confidence", ["0"])[0] or 0)
        nodes: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []
        for paper in rows("SELECT id, title, status FROM papers"):
            nodes[f"paper-{paper['id']}"] = {"id": f"paper-{paper['id']}", "label": paper["title"], "type": "paper", "status": paper["status"]}
        for concept in rows("SELECT * FROM concepts"):
            nodes[f"concept-{concept['id']}"] = {"id": f"concept-{concept['id']}", "label": concept["name"], "type": "concept", "confidence": concept["confidence"], "type_tag": concept["type_tag"]}
            links.append({"source": f"paper-{concept['paper_id']}", "target": f"concept-{concept['id']}", "type": "extracts", "confidence": concept["confidence"]})
        for chunk in rows("SELECT c.id, c.module_path, c.signature, r.name as repo_name FROM code_chunks c JOIN repositories r ON r.id = c.repo_id"):
            nodes[f"code-{chunk['id']}"] = {"id": f"code-{chunk['id']}", "label": chunk["signature"], "type": "code", "path": chunk["module_path"], "repo": chunk["repo_name"]}
        for match in rows("SELECT * FROM matches WHERE confidence >= ?", (confidence,)):
            if match["concept_id"]:
                links.append({"source": f"concept-{match['concept_id']}", "target": f"code-{match['chunk_id']}", "type": match["classification"], "confidence": match["confidence"], "review_state": match["review_state"]})
        return {"nodes": list(nodes.values()), "links": links}
    if path == "/api/timeline":
        return rows("SELECT * FROM activity ORDER BY created_at DESC LIMIT 300")
    if path == "/api/settings":
        return {item["key"]: item["value"] for item in rows("SELECT * FROM settings")}
    raise KeyError("Unknown API route.")


def route_post(path: str, payload: dict[str, Any], headers: dict[str, str] | None = None, raw_body: bytes = b"") -> Any:
    headers = headers or {}
    if path == "/api/ingest/url":
        source_type = payload.get("source_type", "arxiv")
        ids = []
        for value in [line.strip() for line in payload.get("text", "").splitlines() if line.strip()]:
            try:
                if source_type == "doi" or value.startswith("10.") or "doi.org/" in value:
                    paper_id = ingest_doi(value)
                elif "arxiv.org" in value or re.search(r"\b\d{4}\.\d{4,5}", value):
                    paper_id = fetch_arxiv(value)
                else:
                    paper_id = ingest_article_url(value)
            except Exception as exc:
                error_log("ingest_url", str(exc), {"value": value})
                paper_id = store_paper({"title": value, "authors": [], "abstract": f"Could not fetch automatically: {exc}"}, f"Manual URL placeholder for {value}", source_type, value)
            extract_paper(paper_id)
            run_matching(paper_id)
            ids.append(paper_id)
        return {"paper_ids": ids}
    if path == "/api/ingest/bibtex":
        ids = ingest_bibtex(payload.get("bibtex", ""))
        for paper_id in ids:
            extract_paper(paper_id)
            run_matching(paper_id)
        return {"paper_ids": ids}
    if path == "/api/ingest/pdf":
        filename, data = parse_multipart(headers.get("content-type", ""), raw_body)
        paper_id = ingest_pdf_placeholder(filename, data)
        extract_paper(paper_id)
        run_matching(paper_id)
        return {"paper_id": paper_id}
    match = re.fullmatch(r"/api/papers/(\d+)/extract", path)
    if match:
        paper_id = int(match.group(1))
        extract_paper(paper_id)
        run_matching(paper_id)
        return {"queued": False, "done": True}
    match = re.fullmatch(r"/api/papers/(\d+)/notes", path)
    if match:
        paper_id = int(match.group(1))
        with connect() as conn:
            cur = conn.execute("INSERT INTO notes(paper_id, body, updated_at) VALUES (?, ?, ?)", (paper_id, payload.get("body", ""), now()))
        activity("note", "Paper note saved", payload.get("body", "")[:120], "paper", paper_id)
        return {"id": int(cur.lastrowid)}
    if path == "/api/repos":
        repo_id = add_repo(payload.get("path", ""))
        stats = index_repo(repo_id)
        return {"repo_id": repo_id, **stats}
    match = re.fullmatch(r"/api/repos/(\d+)/index", path)
    if match:
        return index_repo(int(match.group(1)))
    if path == "/api/repos/index-all":
        stats = [index_repo(item["id"]) for item in rows("SELECT id FROM repositories")]
        return {"queued": False, "indexed": stats}
    if path == "/api/match/run":
        return run_matching(None)
    if path == "/api/sample-data":
        return load_sample_data()
    raise KeyError("Unknown API route.")


def route_patch(path: str, payload: dict[str, Any]) -> Any:
    match = re.fullmatch(r"/api/matches/(\d+)", path)
    if match:
        match_id = int(match.group(1))
        state = payload.get("review_state", "pending")
        if state not in {"pending", "accepted", "rejected"}:
            raise ValueError("Invalid review_state.")
        with connect() as conn:
            conn.execute("UPDATE matches SET review_state = ? WHERE id = ?", (state, match_id))
        activity("review", f"Match {state}", f"Match #{match_id}", "match", match_id)
        return {"ok": True}
    if path == "/api/settings":
        with connect() as conn:
            for key in ("ollama_endpoint", "ollama_model"):
                if key in payload:
                    conn.execute("INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", (key, str(payload[key]), now()))
        activity("settings", "Settings updated")
        return {"ok": True}
    raise KeyError("Unknown API route.")


def route_delete(path: str) -> Any:
    if path == "/api/database":
        with connect() as conn:
            for table in ("matches", "notes", "code_chunks", "repositories", "paper_items", "code_patterns", "algorithms", "concepts", "papers", "activity", "errors"):
                conn.execute(f"DELETE FROM {table}")
        activity("database", "Database cleared")
        return {"ok": True}
    raise KeyError("Unknown API route.")


class Handler(BaseHTTPRequestHandler):
    server_version = "ReadSyncPython314/1.0"

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def send_json(self, data: Any, status: int = 200) -> None:
        body = dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, exc: Exception, status: int = 400) -> None:
        self.send_json({"detail": str(exc)}, status)

    def read_payload(self) -> tuple[dict[str, Any], bytes]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type and body:
            return json.loads(body.decode("utf-8")), body
        return {}, body

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            try:
                self.send_json(route_get(parsed.path, urllib.parse.parse_qs(parsed.query)))
            except KeyError as exc:
                self.send_error_json(exc, 404)
            except Exception as exc:
                error_log("get", str(exc), {"path": parsed.path})
                self.send_error_json(exc, 400)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        payload, raw = self.read_payload()
        try:
            self.send_json(route_post(parsed.path, payload, dict(self.headers), raw))
        except KeyError as exc:
            self.send_error_json(exc, 404)
        except Exception as exc:
            error_log("post", str(exc), {"path": parsed.path})
            self.send_error_json(exc, 400)

    def do_PATCH(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        payload, _ = self.read_payload()
        try:
            self.send_json(route_patch(parsed.path, payload))
        except KeyError as exc:
            self.send_error_json(exc, 404)
        except Exception as exc:
            self.send_error_json(exc, 400)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            self.send_json(route_delete(parsed.path))
        except KeyError as exc:
            self.send_error_json(exc, 404)
        except Exception as exc:
            self.send_error_json(exc, 400)

    def serve_static(self, path: str) -> None:
        if not FRONTEND_DIST.exists():
            self.send_error_json(RuntimeError("Frontend build is missing. Run npm install && npm run build in frontend, or run ./setup.sh."), 500)
            return
        requested = FRONTEND_DIST / path.lstrip("/")
        target = requested if requested.exists() and requested.is_file() else FRONTEND_DIST / "index.html"
        content_type = "text/html"
        if target.suffix == ".js":
            content_type = "application/javascript"
        elif target.suffix == ".css":
            content_type = "text/css"
        elif target.suffix == ".svg":
            content_type = "image/svg+xml"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[ReadSync] {self.address_string()} - {fmt % args}")


def main() -> None:
    init_db()
    host = "127.0.0.1"
    port = int(os.environ.get("READSYNC_PORT", "8000"))
    print("ReadSync Python 3.14 runtime")
    print(f"Database: {DB_PATH}")
    print(f"Open: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    if "--sample-data" in sys.argv:
        init_db()
        print(json.dumps(load_sample_data(), indent=2))
    else:
        main()
