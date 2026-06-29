import ast
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from ..config import settings
from ..db import connect, dumps, log_activity, log_error, utcnow
from .embeddings import embed_text, embedding_to_json, keyword_fingerprint


EXT_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".html": "html",
    ".htm": "html",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

MAX_SOURCE_BYTES = 900_000
MAX_ZIP_BYTES = 80_000_000


def _is_git_url(value: str) -> bool:
    value = value.strip()
    return (
        value.startswith("https://github.com/")
        or value.startswith("http://github.com/")
        or value.startswith("git@github.com:")
        or value.endswith(".git") and ("://" in value or value.startswith("git@"))
    )


def _repo_slug_from_url(url: str) -> str:
    cleaned = url.strip().removesuffix(".git")
    if cleaned.startswith("git@github.com:"):
        cleaned = cleaned.replace("git@github.com:", "github.com/", 1)
    parts = [part for part in re.split(r"[/:]+", cleaned) if part and part not in {"https", "http"}]
    if len(parts) >= 3 and parts[-3] == "github.com":
        owner, repo = parts[-2], parts[-1]
    elif len(parts) >= 2:
        owner, repo = parts[-2], parts[-1]
    else:
        owner, repo = "external", "repository"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{owner}-{repo}").strip("-") or "repository"


def _clone_or_update_repository(url: str) -> Path:
    settings.managed_repo_path.mkdir(parents=True, exist_ok=True)
    target = settings.managed_repo_path / _repo_slug_from_url(url)
    if target.exists() and (target / ".git").exists():
        try:
            from git import Repo
            repo = Repo(target)
            repo.remotes.origin.fetch()
            branch = repo.active_branch.name
            repo.remotes.origin.pull(branch)
        except Exception:
            subprocess.run(["git", "-C", str(target), "pull", "--ff-only"], check=False, capture_output=True)
        return target
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"Managed clone target already exists and is not an empty Git repository: {target}")
    if target.exists():
        shutil.rmtree(target)
    try:
        from git import Repo
        Repo.clone_from(url, target)
    except Exception as exc:
        try:
            subprocess.run(["git", "clone", url, str(target)], check=True, capture_output=True, text=True)
        except Exception as sub_exc:
            raise ValueError(f"Could not clone GitHub repository. Check the URL and your internet connection. Details: {exc or sub_exc}") from sub_exc
    return target


def _tree_sitter_language(language: str):
    try:
        from tree_sitter import Language
        if language == "javascript":
            import tree_sitter_javascript as grammar
            raw = grammar.language()
        elif language == "typescript":
            import tree_sitter_typescript as grammar
            raw = grammar.language_typescript()
        elif language == "cpp":
            import tree_sitter_cpp as grammar
            raw = grammar.language()
        elif language == "c":
            import tree_sitter_c as grammar
            raw = grammar.language()
        else:
            return None
        try:
            return Language(raw)
        except TypeError:
            return raw
    except Exception:
        return None


def _tree_sitter_chunks(path: Path, source: str, language: str) -> list[dict[str, Any]]:
    tree_language = _tree_sitter_language(language)
    if tree_language is None:
        return []
    try:
        from tree_sitter import Parser
        parser = Parser()
        try:
            parser.language = tree_language
        except Exception:
            parser.set_language(tree_language)
        tree = parser.parse(source.encode("utf-8"))
    except Exception:
        return []

    lines = source.splitlines()
    imports = [
        line.strip()
        for line in lines
        if line.strip().startswith(("import ", "export ", "#include", "using "))
    ][:40]
    function_types = {
        "function_declaration",
        "function_definition",
        "method_definition",
        "generator_function_declaration",
        "class_declaration",
        "class_specifier",
        "struct_specifier",
    }
    expression_types = {"arrow_function", "function_expression"}
    chunks: list[dict[str, Any]] = []

    def owning_statement(node):
        current = node
        while current.parent is not None and current.parent.type in {
            "variable_declarator",
            "lexical_declaration",
            "assignment_expression",
            "export_statement",
            "parenthesized_expression",
        }:
            current = current.parent
        return current

    def walk(node):
        candidate = node
        if node.type in expression_types:
            candidate = owning_statement(node)
        if node.type in function_types or node.type in expression_types:
            start = candidate.start_point[0] + 1
            end = candidate.end_point[0] + 1
            if end >= start:
                body = "\n".join(lines[start - 1:end])
                signature = lines[start - 1].strip()[:220] if start - 1 < len(lines) else path.name
                calls = sorted(set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body)))[:80]
                chunks.append({
                    "signature": signature,
                    "class_name": "",
                    "docstring": "",
                    "body": body,
                    "imports": imports,
                    "calls": calls,
                    "start_line": start,
                    "end_line": end,
                    "parser": "tree-sitter",
                })
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    deduped = {}
    for chunk in chunks:
        key = (chunk["start_line"], chunk["end_line"], chunk["signature"])
        deduped[key] = chunk
    return list(deduped.values())


def _git_info(repo_path: Path, file_path: Path) -> tuple[str, str]:
    try:
        from git import Repo
        repo = Repo(repo_path, search_parent_directories=True)
        rel = str(file_path.relative_to(repo.working_tree_dir))
        commits = list(repo.iter_commits(paths=rel, max_count=1))
        if commits:
            commit = commits[0]
            return commit.hexsha[:10], commit.committed_datetime.isoformat()
    except Exception:
        pass
    try:
        return "", __import__("datetime").datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
    except Exception:
        return "", ""


def _python_chunks(path: Path, source: str) -> list[dict[str, Any]]:
    tree = ast.parse(source)
    lines = source.splitlines()
    imports = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.get_source_segment(source, node) or "")
    chunks = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            class_name = ""
            signature = node.name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                signature = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}({', '.join(arg.arg for arg in node.args.args)})"
            else:
                signature = f"class {node.name}"
            body = "\n".join(lines[start - 1:end])
            docstring = ast.get_docstring(node) or ""
            calls = sorted({n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)})
            chunks.append({
                "signature": signature,
                "class_name": class_name,
                "docstring": docstring,
                "body": body,
                "imports": imports,
                "calls": calls,
                "start_line": start,
                "end_line": end,
            })
    return chunks


def _regex_chunks(path: Path, source: str, language: str) -> list[dict[str, Any]]:
    lines = source.splitlines()
    pattern = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+[\w$]+|const\s+[\w$]+\s*=\s*(?:async\s*)?\(|class\s+[\w$]+|[\w:<>&*\s]+\s+[\w:~]+\s*\([^;]*\)\s*\{)")
    starts = [idx + 1 for idx, line in enumerate(lines) if pattern.search(line)]
    if not starts:
        starts = [1] if source.strip() else []
    chunks = []
    for pos, start in enumerate(starts[:240]):
        end = (starts[pos + 1] - 1) if pos + 1 < len(starts) else min(len(lines), start + 120)
        body = "\n".join(lines[start - 1:end])
        signature = lines[start - 1].strip()[:220] if lines else path.name
        imports = [line.strip() for line in lines if line.strip().startswith(("import ", "#include", "const "))][:30]
        calls = sorted(set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body)))[:60]
        chunks.append({
            "signature": signature,
            "class_name": "",
            "docstring": "",
            "body": body,
            "imports": imports,
            "calls": calls,
            "start_line": start,
            "end_line": end,
        })
    return chunks


def _html_chunks(path: Path, source: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    lines_before = lambda index: source[:index].count("\n") + 1
    for idx, match in enumerate(re.finditer(r"<script\b[^>]*>(.*?)</script>", source, flags=re.I | re.S), start=1):
        script = match.group(1).strip()
        if not script:
            continue
        start_line = lines_before(match.start(1))
        script_chunks = _tree_sitter_chunks(path, script, "javascript") or _regex_chunks(path, script, "javascript")
        for chunk in script_chunks:
            chunk["start_line"] = start_line + chunk["start_line"] - 1
            chunk["end_line"] = start_line + chunk["end_line"] - 1
            chunk["signature"] = f"script[{idx}] · {chunk['signature']}"
            chunk["language"] = "html/javascript"
            chunks.append(chunk)
        if not script_chunks:
            calls = sorted(set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", script)))[:80]
            chunks.append({
                "signature": f"script[{idx}] inline JavaScript block",
                "class_name": "",
                "docstring": "",
                "body": script[:20000],
                "imports": [],
                "calls": calls,
                "start_line": start_line,
                "end_line": start_line + script.count("\n"),
                "language": "html/javascript",
            })
    if chunks:
        return chunks
    title_match = re.search(r"<title[^>]*>(.*?)</title>", source, flags=re.I | re.S)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else path.name
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", source, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text:
        chunks.append({
            "signature": f"HTML document · {title[:140]}",
            "class_name": "",
            "docstring": "",
            "body": text[:20000],
            "imports": [],
            "calls": [],
            "start_line": 1,
            "end_line": max(1, source.count("\n") + 1),
            "language": "html",
        })
    return chunks


def extract_chunks(path: Path, repo_path: Path) -> list[dict[str, Any]]:
    ext = path.suffix.lower()
    language = EXT_LANG.get(ext, "text")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        return []
    source = path.read_text(errors="ignore")
    try:
        if language == "python":
            chunks = _python_chunks(path, source)
        elif language == "html":
            chunks = _html_chunks(path, source)
        else:
            chunks = _tree_sitter_chunks(path, source, language) or _regex_chunks(path, source, language)
    except Exception:
        chunks = _regex_chunks(path, source, language)
    commit, modified = _git_info(repo_path, path)
    for chunk in chunks:
        chunk["language"] = language
        chunk["file_path"] = str(path)
        chunk["module_path"] = str(path.relative_to(repo_path)) if path.is_relative_to(repo_path) else str(path)
        chunk["last_commit"] = commit
        chunk["last_modified"] = modified
        chunk["structural_text"] = " ".join([chunk["signature"], " ".join(chunk["imports"]), " ".join(chunk["calls"])])
        emb_text = "\n".join([chunk["signature"], chunk["docstring"], chunk["body"][:9000]])
        chunk["embedding"] = embed_text(emb_text)
    return chunks


def add_repository(path: str) -> int:
    input_value = path.strip()
    if _is_git_url(input_value):
        repo_path = _clone_or_update_repository(input_value)
    else:
        repo_path = Path(input_value).expanduser().resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise ValueError("Repository path does not exist or is not a directory. Paste a local folder path, or paste a GitHub repository URL so ReadSync can clone it.")
    with connect() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO repositories(name, path, status, created_at) VALUES (?, ?, 'pending', ?)",
            (repo_path.name, str(repo_path), utcnow()),
        )
        repo_id = int(cur.lastrowid or con.execute("SELECT id FROM repositories WHERE path = ?", (str(repo_path),)).fetchone()["id"])
    log_activity("repo", "Repository added", str(repo_path), "repo", repo_id)
    return repo_id


def add_repository_from_zip(filename: str, data: bytes) -> int:
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError("ZIP archive is too large for local import.")
    if not filename.lower().endswith(".zip"):
        raise ValueError("Only .zip code archives are supported.")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(filename).stem).strip("-") or "uploaded-code"
    target = settings.managed_repo_path / f"upload-{slug}"
    settings.managed_repo_path.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                destination = (target / member.filename).resolve()
                if not str(destination).startswith(str(target.resolve())):
                    raise ValueError("Unsafe ZIP archive path detected.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded file is not a valid ZIP archive.") from exc
    return add_repository(str(target))


def index_repository(repo_id: int) -> dict:
    try:
        with connect() as con:
            repo = con.execute("SELECT * FROM repositories WHERE id = ?", (repo_id,)).fetchone()
            if not repo:
                raise ValueError("Repository not found.")
            con.execute("UPDATE repositories SET status = 'indexing' WHERE id = ?", (repo_id,))
        repo_path = Path(repo["path"])
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f"Repository path is no longer available: {repo_path}")
        ignored_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".cache"}
        paths = []
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [dirname for dirname in dirs if dirname not in ignored_dirs and not dirname.startswith(".mypy")]
            for filename in files:
                path = Path(root) / filename
                if path.suffix.lower() in EXT_LANG:
                    paths.append(path)

        prepared_chunks = []
        for path in paths:
            try:
                prepared_chunks.extend(extract_chunks(path, repo_path))
            except Exception as exc:
                log_error("index_file", str(exc), {"path": str(path), "repo_id": repo_id})

        rows = []
        now = utcnow()
        for chunk in prepared_chunks:
            rows.append((
                repo_id,
                chunk["file_path"],
                chunk["language"],
                chunk["module_path"],
                chunk["class_name"],
                chunk["signature"],
                chunk["docstring"],
                chunk["body"],
                dumps(chunk["imports"]),
                dumps(chunk["calls"]),
                chunk["start_line"],
                chunk["end_line"],
                chunk["last_commit"],
                chunk["last_modified"],
                embedding_to_json(chunk["embedding"]),
                " ".join(keyword_fingerprint(chunk["structural_text"])),
                now,
            ))

        with connect() as con:
            con.execute("DELETE FROM code_chunks WHERE repo_id = ?", (repo_id,))
            con.executemany(
                """
                INSERT INTO code_chunks(repo_id, file_path, language, module_path, class_name, signature, docstring, body, imports_json, calls_json, start_line, end_line, last_commit, last_modified, embedding_json, structural_text, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            con.execute("UPDATE repositories SET status = 'indexed', last_indexed_at = ?, chunk_count = ? WHERE id = ?", (utcnow(), len(rows), repo_id))
        log_activity("index", "Repository indexed", f"{repo_path} · {len(rows)} chunks", "repo", repo_id)
        return {"repo_id": repo_id, "chunks": len(rows), "files_scanned": len(paths)}
    except Exception as exc:
        log_error("index_repository", str(exc), {"repo_id": repo_id})
        with connect() as con:
            con.execute("UPDATE repositories SET status = 'error' WHERE id = ?", (repo_id,))
        raise
