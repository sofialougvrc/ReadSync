import json
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import all_rows, connect, dumps, init_db, loads, log_activity, log_error, one, utcnow
from .schemas import BibtexIngestRequest, MatchReviewRequest, NoteRequest, RepositoryRequest, SettingsRequest, UrlIngestRequest
from .services.code_indexer import add_repository, add_repository_from_zip, index_repository
from .services.extraction import run_extraction
from .services.ingestion import fetch_arxiv, ingest_article_url, ingest_bibtex, ingest_doi, ingest_pdf_upload
from .services.matcher import run_matching


app = FastAPI(title="ReadSync", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def parse_row(row: dict) -> dict:
    parsed = dict(row)
    for key in list(parsed):
        if key.endswith("_json"):
            parsed[key[:-5]] = loads(parsed.pop(key), [])
    return parsed


def background_extract_and_match(paper_id: int) -> None:
    try:
        run_extraction(paper_id)
        run_matching(paper_id)
    except Exception as exc:
        log_error("background_extract_match", str(exc), {"paper_id": paper_id}, paper_id)
        with connect() as con:
            con.execute("UPDATE papers SET status = 'error', extraction_error = ?, updated_at = ? WHERE id = ?", (str(exc), utcnow(), paper_id))


def process_all_papers() -> dict:
    papers = all_rows("SELECT id, status FROM papers ORDER BY id")
    processed = []
    failed = []
    for paper in papers:
        paper_id = paper["id"]
        try:
            idea_count = one("SELECT (SELECT COUNT(*) FROM concepts WHERE paper_id = ?) + (SELECT COUNT(*) FROM algorithms WHERE paper_id = ?) AS count", (paper_id, paper_id))["count"]
            weak_count = one(
                """
                SELECT COUNT(*) AS count
                FROM concepts
                WHERE paper_id = ?
                  AND (
                    LENGTH(description) - LENGTH(REPLACE(description, ' ', '')) < 45
                    OR description LIKE '%appears relevant to%'
                    OR description LIKE '%because the paper discusses%'
                  )
                """,
                (paper_id,),
            )["count"]
            if paper["status"] != "extracted" or idea_count == 0 or weak_count:
                run_extraction(paper_id)
            result = run_matching(paper_id)
            processed.append({"paper_id": paper_id, **result})
        except Exception as exc:
            failed.append({"paper_id": paper_id, "error": str(exc)})
    return {"processed": processed, "failed": failed, "processed_count": len(processed), "failed_count": len(failed)}


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "database": str(settings.sqlite_path),
        "ollama_endpoint": settings.ollama_endpoint,
        "ollama_model": settings.ollama_model,
    }


@app.get("/api/dashboard")
def dashboard():
    stats = {
        "papers": one("SELECT COUNT(*) as count FROM papers")["count"],
        "concepts": one("SELECT COUNT(*) as count FROM concepts")["count"],
        "repos": one("SELECT COUNT(*) as count FROM repositories")["count"],
        "chunks": one("SELECT COUNT(*) as count FROM code_chunks")["count"],
        "pending": one("SELECT COUNT(*) as count FROM matches WHERE review_state = 'pending'")["count"],
        "accepted": one("SELECT COUNT(*) as count FROM matches WHERE review_state = 'accepted'")["count"],
        "errors": one("SELECT COUNT(*) as count FROM papers WHERE status = 'error'")["count"],
    }
    recent = [parse_row(row) for row in all_rows("SELECT * FROM activity ORDER BY id DESC LIMIT 20")]
    pending = [parse_row(row) for row in all_rows(
        """
        SELECT m.*, p.title as paper_title, COALESCE(c.name, a.name) as idea_name, ch.module_path, ch.signature, r.name as repo_name
        FROM matches m
        JOIN papers p ON p.id = m.paper_id
        LEFT JOIN concepts c ON c.id = m.concept_id
        LEFT JOIN algorithms a ON a.id = m.algorithm_id
        JOIN code_chunks ch ON ch.id = m.chunk_id
        JOIN repositories r ON r.id = ch.repo_id
        WHERE m.review_state = 'pending'
        ORDER BY m.confidence DESC
        LIMIT 12
        """
    )]
    return {"stats": stats, "recent": recent, "pending": pending}


@app.post("/api/ingest/url")
def ingest_url(request: UrlIngestRequest, background: BackgroundTasks):
    ids = []
    errors = []
    lines = [line.strip() for line in request.text.splitlines() if line.strip()]
    for value in lines:
        try:
            if request.source_type == "doi" or value.startswith("10.") or "doi.org/" in value:
                paper_id = ingest_doi(value)
            elif "arxiv.org" in value or __import__("re").search(r"\b\d{4}\.\d{4,5}", value):
                paper_id = fetch_arxiv(value)
            else:
                paper_id = ingest_article_url(value)
            ids.append(paper_id)
            background.add_task(background_extract_and_match, paper_id)
        except Exception as exc:
            log_error("url_ingest", str(exc), {"value": value})
            errors.append({"input": value, "error": str(exc)})
    if not ids and errors:
        raise HTTPException(status_code=400, detail=errors[0]["error"])
    return {"paper_ids": ids, "errors": errors}


@app.post("/api/ingest/bibtex")
def ingest_bibtex_endpoint(request: BibtexIngestRequest, background: BackgroundTasks):
    try:
        ids = ingest_bibtex(request.bibtex)
        for paper_id in ids:
            background.add_task(background_extract_and_match, paper_id)
        return {"paper_ids": ids}
    except Exception as exc:
        log_error("bibtex_ingest", str(exc), {})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ingest/pdf")
async def ingest_pdf_endpoint(background: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    data = await file.read()
    try:
        paper_id = ingest_pdf_upload(file.filename, data)
        background.add_task(background_extract_and_match, paper_id)
        return {"paper_id": paper_id}
    except Exception as exc:
        log_error("pdf_ingest", str(exc), {"filename": file.filename})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/papers")
def papers(q: str = "", status: str = ""):
    clauses = []
    params = []
    if q:
        clauses.append("(title LIKE ? OR abstract LIKE ? OR raw_text LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = all_rows(f"SELECT id, title, authors_json, abstract, year, categories_json, arxiv_id, doi, source_type, status, created_at, updated_at FROM papers {where} ORDER BY updated_at DESC", params)
    return [parse_row(row) for row in rows]


@app.get("/api/papers/{paper_id}")
def paper_detail(paper_id: int):
    paper = one("SELECT * FROM papers WHERE id = ?", (paper_id,))
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found.")
    detail = parse_row(paper)
    detail["concepts"] = all_rows("SELECT * FROM concepts WHERE paper_id = ? ORDER BY confidence DESC", (paper_id,))
    detail["algorithms"] = all_rows("SELECT * FROM algorithms WHERE paper_id = ? ORDER BY confidence DESC", (paper_id,))
    detail["code_patterns"] = all_rows("SELECT * FROM code_patterns WHERE paper_id = ? ORDER BY confidence DESC", (paper_id,))
    detail["items"] = all_rows("SELECT * FROM paper_items WHERE paper_id = ? ORDER BY kind, label", (paper_id,))
    detail["matches"] = [parse_row(row) for row in all_rows(
        """
        SELECT m.*, COALESCE(c.name, a.name) as idea_name, ch.module_path, ch.signature, ch.start_line, ch.end_line, ch.body, r.name as repo_name
        FROM matches m
        LEFT JOIN concepts c ON c.id = m.concept_id
        LEFT JOIN algorithms a ON a.id = m.algorithm_id
        JOIN code_chunks ch ON ch.id = m.chunk_id
        JOIN repositories r ON r.id = ch.repo_id
        WHERE m.paper_id = ?
        ORDER BY m.confidence DESC
        """,
        (paper_id,),
    )]
    detail["notes"] = all_rows("SELECT * FROM notes WHERE paper_id = ? ORDER BY updated_at DESC", (paper_id,))
    return detail


@app.post("/api/papers/{paper_id}/extract")
def extract_paper(paper_id: int):
    extraction = run_extraction(paper_id)
    match_result = run_matching(paper_id)
    return {"queued": False, "extraction": extraction, "matching": match_result}


@app.post("/api/papers/{paper_id}/match")
def match_paper(paper_id: int):
    return run_matching(paper_id)


@app.post("/api/papers/{paper_id}/notes")
def save_note(paper_id: int, request: NoteRequest):
    with connect() as con:
        cur = con.execute("INSERT INTO notes(paper_id, body, updated_at) VALUES (?, ?, ?)", (paper_id, request.body, utcnow()))
    log_activity("note", "Paper note saved", request.body[:120], "paper", paper_id)
    return {"id": int(cur.lastrowid)}


@app.get("/api/repos")
def repos():
    return all_rows("SELECT * FROM repositories ORDER BY created_at DESC")


@app.post("/api/repos")
def create_repo(request: RepositoryRequest):
    try:
        repo_id = add_repository(request.path)
        result = index_repository(repo_id)
        return {"repo_id": repo_id, **result}
    except Exception as exc:
        log_error("repo_add", str(exc), {"path": request.path})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/repos/upload")
async def upload_repo_zip(file: UploadFile = File(...)):
    try:
        data = await file.read()
        repo_id = add_repository_from_zip(file.filename, data)
        result = index_repository(repo_id)
        return {"repo_id": repo_id, **result}
    except Exception as exc:
        log_error("repo_upload", str(exc), {"filename": file.filename})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/repos/{repo_id}/index")
def reindex_repo(repo_id: int):
    return index_repository(repo_id)


@app.post("/api/repos/index-all")
def reindex_all():
    results = []
    for repo in all_rows("SELECT id FROM repositories"):
        results.append(index_repository(repo["id"]))
    return {"results": results, "repo_count": len(results), "chunk_count": sum(item.get("chunks", 0) for item in results)}


@app.get("/api/chunks")
def chunks(repo_id: int | None = None, q: str = ""):
    clauses = []
    params = []
    if repo_id:
        clauses.append("c.repo_id = ?")
        params.append(repo_id)
    if q:
        clauses.append("(c.module_path LIKE ? OR c.signature LIKE ? OR c.body LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return [parse_row(row) for row in all_rows(
        f"""
        SELECT c.*, r.name as repo_name
        FROM code_chunks c JOIN repositories r ON r.id = c.repo_id
        {where}
        ORDER BY c.module_path, c.start_line
        LIMIT 500
        """,
        params,
    )]


@app.post("/api/match/run")
def match_run(paper_id: int | None = None):
    return run_matching(paper_id)


@app.post("/api/process/all")
def process_all():
    return process_all_papers()


@app.get("/api/matches")
def matches(review_state: str = ""):
    params = []
    where = ""
    if review_state:
        where = "WHERE m.review_state = ?"
        params.append(review_state)
    return [parse_row(row) for row in all_rows(
        f"""
        SELECT m.*, p.title as paper_title, COALESCE(c.name, a.name) as idea_name, ch.module_path, ch.signature, ch.body, r.name as repo_name
        FROM matches m
        JOIN papers p ON p.id = m.paper_id
        LEFT JOIN concepts c ON c.id = m.concept_id
        LEFT JOIN algorithms a ON a.id = m.algorithm_id
        JOIN code_chunks ch ON ch.id = m.chunk_id
        JOIN repositories r ON r.id = ch.repo_id
        {where}
        ORDER BY m.created_at DESC
        LIMIT 500
        """,
        params,
    )]


@app.patch("/api/matches/{match_id}")
def review_match(match_id: int, request: MatchReviewRequest):
    with connect() as con:
        con.execute("UPDATE matches SET review_state = ? WHERE id = ?", (request.review_state, match_id))
    log_activity("review", f"Match {request.review_state}", f"Match #{match_id}", "match", match_id)
    return {"ok": True}


@app.get("/api/graph")
def graph(confidence: float = 0.0, review_state: str = ""):
    nodes: dict[str, dict] = {}
    links = []
    papers = all_rows("SELECT id, title, year, status FROM papers")
    for paper in papers:
        nodes[f"paper-{paper['id']}"] = {"id": f"paper-{paper['id']}", "label": paper["title"], "type": "paper", "status": paper["status"]}
    concepts = all_rows("SELECT * FROM concepts")
    for concept in concepts:
        nodes[f"concept-{concept['id']}"] = {"id": f"concept-{concept['id']}", "label": concept["name"], "type": "concept", "confidence": concept["confidence"], "type_tag": concept["type_tag"]}
        links.append({"source": f"paper-{concept['paper_id']}", "target": f"concept-{concept['id']}", "type": "extracts", "confidence": concept["confidence"]})
    chunks_rows = all_rows("SELECT c.id, c.module_path, c.signature, r.name as repo_name FROM code_chunks c JOIN repositories r ON r.id = c.repo_id")
    for chunk in chunks_rows:
        nodes[f"code-{chunk['id']}"] = {"id": f"code-{chunk['id']}", "label": chunk["signature"], "type": "code", "path": chunk["module_path"], "repo": chunk["repo_name"]}
    clauses = ["m.confidence >= ?"]
    params = [confidence]
    if review_state:
        clauses.append("m.review_state = ?")
        params.append(review_state)
    for match in all_rows(f"SELECT * FROM matches m WHERE {' AND '.join(clauses)}", params):
        if match["concept_id"]:
            source = f"concept-{match['concept_id']}"
        elif match["algorithm_id"]:
            algo_key = f"algorithm-{match['algorithm_id']}"
            nodes.setdefault(algo_key, {"id": algo_key, "label": f"Algorithm {match['algorithm_id']}", "type": "concept"})
            source = algo_key
        else:
            continue
        links.append({
            "source": source,
            "target": f"code-{match['chunk_id']}",
            "type": match["classification"],
            "confidence": match["confidence"],
            "review_state": match["review_state"],
        })
    return {"nodes": list(nodes.values()), "links": links}


@app.get("/api/timeline")
def timeline():
    return all_rows("SELECT * FROM activity ORDER BY created_at DESC LIMIT 300")


@app.get("/api/settings")
def get_settings():
    rows = all_rows("SELECT * FROM settings")
    return {row["key"]: row["value"] for row in rows}


@app.patch("/api/settings")
def update_settings(request: SettingsRequest):
    updates = request.model_dump(exclude_none=True)
    with connect() as con:
        for key, value in updates.items():
            con.execute("INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", (key, value, utcnow()))
    log_activity("settings", "Settings updated", ", ".join(updates.keys()))
    return {"ok": True}


@app.delete("/api/database")
def clear_database():
    with connect() as con:
        for table in ("matches", "notes", "code_chunks", "repositories", "paper_items", "code_patterns", "algorithms", "concepts", "papers", "activity", "errors"):
            con.execute(f"DELETE FROM {table}")
    log_activity("database", "Database cleared")
    return {"ok": True}


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        target = frontend_dist / full_path
        if target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(frontend_dist / "index.html")
