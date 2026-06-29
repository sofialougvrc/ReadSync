import hashlib
import re
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..db import all_rows, connect, dumps, log_activity, log_error, utcnow


MIN_EXTRACTED_TEXT_CHARS = 180
BAD_CONTENT_MARKERS = (
    "checking your browser before accessing",
    "enable javascript and cookies",
    "cloudflare ray id",
    "access denied",
    "captcha",
    "just a moment...",
)


def _clean_text(text: str) -> str:
    text = (text or "").replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _validate_extracted_text(raw_text: str, source: str) -> str:
    clean = _clean_text(raw_text)
    lowered = clean.lower()
    if any(marker in lowered for marker in BAD_CONTENT_MARKERS):
        raise ValueError(f"ReadSync could not ingest {source}: the source returned an access-check or bot-protection page instead of readable paper text.")
    if len(clean) < MIN_EXTRACTED_TEXT_CHARS:
        raise ValueError(f"ReadSync could not ingest {source}: extracted text was too short to analyze reliably.")
    return clean


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _arxiv_id(value: str) -> str:
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([A-Za-z0-9.\-]+)", value)
    if match:
        return match.group(1).replace(".pdf", "")
    match = re.search(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b", value)
    return match.group(0) if match else ""


def dedupe_lookup(title: str, authors: list[str], arxiv_id: str = "", doi: str = "") -> dict[str, Any] | None:
    rows = all_rows("SELECT * FROM papers")
    author_key = " ".join(authors[:2]).lower()
    title_key = _title_key(title)
    for row in rows:
        if arxiv_id and row.get("arxiv_id") == arxiv_id:
            return row
        if doi and row.get("doi") == doi:
            return row
        row_title = _title_key(row.get("title", ""))
        row_authors = " ".join(__import__("json").loads(row.get("authors_json") or "[]")[:2]).lower()
        if title_key and row_title and (title_key in row_title or row_title in title_key) and (not author_key or author_key[:18] in row_authors):
            return row
    return None


def store_paper(meta: dict[str, Any], raw_text: str, source_type: str, source_url: str = "") -> int:
    title = meta.get("title") or "Untitled paper"
    authors = meta.get("authors") or []
    arxiv_id = meta.get("arxiv_id") or ""
    doi = meta.get("doi") or ""
    existing = dedupe_lookup(title, authors, arxiv_id, doi)
    now = utcnow()
    if existing:
        with connect() as con:
            con.execute(
                "UPDATE papers SET raw_text = COALESCE(NULLIF(?, ''), raw_text), source_url = COALESCE(NULLIF(?, ''), source_url), updated_at = ? WHERE id = ?",
                (_clean_text(raw_text), source_url, now, existing["id"]),
            )
        log_activity("dedupe", "Existing paper reused", title, "paper", existing["id"])
        return int(existing["id"])
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO papers(title, authors_json, abstract, year, categories_json, arxiv_id, doi, source_url, source_type, raw_text, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ingested', ?, ?)
            """,
            (
                title,
                dumps(authors),
                meta.get("abstract") or "",
                meta.get("year"),
                dumps(meta.get("categories") or []),
                arxiv_id,
                doi,
                source_url,
                source_type,
                _clean_text(raw_text) or "",
                now,
                now,
            ),
        )
        paper_id = int(cur.lastrowid)
    log_activity("ingest", "Paper ingested", title, "paper", paper_id)
    return paper_id


def fetch_arxiv(value: str) -> int:
    arxiv_id = _arxiv_id(value)
    if not arxiv_id:
        raise ValueError("No arXiv ID found.")
    url = f"http://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_id)}"
    with urllib.request.urlopen(url, timeout=30) as response:
        xml = response.read()
    root = ET.fromstring(xml)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        raise ValueError(f"arXiv record not found: {arxiv_id}")
    title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
    abstract = " ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split())
    published = entry.findtext("a:published", default="", namespaces=ns) or ""
    authors = [node.findtext("a:name", default="", namespaces=ns) for node in entry.findall("a:author", ns)]
    categories = [node.attrib.get("term", "") for node in entry.findall("a:category", ns)]
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    raw_text = abstract
    try:
        raw_text = extract_pdf_from_url(pdf_url)
    except Exception as exc:
        log_error("arxiv_pdf", str(exc), {"arxiv_id": arxiv_id})
    return store_paper(
        {
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "year": int(published[:4]) if published[:4].isdigit() else None,
            "categories": categories,
            "arxiv_id": arxiv_id,
        },
        _validate_extracted_text(raw_text, value),
        "arxiv",
        value,
    )


def extract_pdf(path: str | Path) -> str:
    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF is required for PDF extraction. Run setup.sh first.") from exc
    with fitz.open(path) as doc:
        pages = []
        for page in doc:
            blocks = page.get_text("blocks", sort=True)
            blocks = sorted(blocks, key=lambda b: (round(b[1] / 40), b[0]))
            pages.append("\n".join(block[4].strip() for block in blocks if block[4].strip()))
    return _clean_text("\n\n".join(pages))


def extract_pdf_from_url(url: str) -> str:
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return extract_pdf(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def ingest_pdf_upload(filename: str, data: bytes) -> int:
    digest = hashlib.sha256(data).hexdigest()[:12]
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        raw_text = extract_pdf(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    raw_text = _validate_extracted_text(raw_text, filename)
    first_line = next((line.strip() for line in raw_text.splitlines() if len(line.strip()) > 12), filename)
    return store_paper(
        {"title": first_line[:220], "authors": [], "abstract": raw_text[:1200], "arxiv_id": "", "doi": f"local-pdf-{digest}"},
        raw_text,
        "pdf",
        filename,
    )


def ingest_article_url(url: str) -> int:
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        raw_text = trafilatura.extract(downloaded, include_comments=False, include_tables=True) or ""
    except Exception as exc:
        log_error("trafilatura", str(exc), {"url": url})
        with urllib.request.urlopen(url, timeout=30) as response:
            html = response.read().decode("utf-8", errors="ignore")
        raw_text = re.sub(r"<[^>]+>", " ", html)
    raw_text = _validate_extracted_text(raw_text, url)
    title = raw_text.strip().splitlines()[0][:180] if raw_text.strip() else url
    return store_paper({"title": title, "authors": [], "abstract": raw_text[:1200]}, raw_text, "article", url)


def ingest_doi(doi: str) -> int:
    clean = doi.strip().replace("https://doi.org/", "")
    request = urllib.request.Request(f"https://api.crossref.org/works/{urllib.parse.quote(clean)}", headers={"User-Agent": "ReadSync local"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = __import__("json").loads(response.read().decode("utf-8"))
    msg = data.get("message", {})
    title = (msg.get("title") or [clean])[0]
    authors = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in msg.get("author", [])]
    year = None
    try:
        year = msg.get("published-print", msg.get("published-online", {})).get("date-parts", [[None]])[0][0]
    except Exception:
        pass
    abstract = re.sub(r"<[^>]+>", " ", msg.get("abstract", ""))
    raw_text = abstract or title
    if len(_clean_text(raw_text)) < MIN_EXTRACTED_TEXT_CHARS:
        raw_text = "\n\n".join(filter(None, [title, abstract, msg.get("container-title", [""])[0] if msg.get("container-title") else "", msg.get("publisher", "")]))
    return store_paper({"title": title, "authors": authors, "year": year, "doi": clean, "abstract": abstract}, raw_text, "doi", f"https://doi.org/{clean}")


def ingest_bibtex(text: str) -> list[int]:
    ids: list[int] = []
    try:
        import bibtexparser
        db = bibtexparser.loads(text)
        entries = db.entries
    except Exception:
        entries = []
        for block in re.findall(r"@\w+\{[^@]+", text, flags=re.S):
            entries.append({k.lower(): v.strip("{} ") for k, v in re.findall(r"(\w+)\s*=\s*[{'\"]([^{}'\"]+)", block)})
    for entry in entries:
        title = entry.get("title") or "Untitled BibTeX entry"
        authors = [name.strip() for name in (entry.get("author") or "").replace("\n", " ").split(" and ") if name.strip()]
        raw = "\n".join(f"{key}: {value}" for key, value in entry.items())
        ids.append(store_paper({
            "title": title,
            "authors": authors,
            "year": int(entry["year"]) if str(entry.get("year", "")).isdigit() else None,
            "doi": entry.get("doi", ""),
            "abstract": entry.get("abstract", ""),
        }, raw, "bibtex", entry.get("url", "")))
    return ids
