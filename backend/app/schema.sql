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
CREATE INDEX IF NOT EXISTS idx_code_chunks_repo ON code_chunks(repo_id);
CREATE INDEX IF NOT EXISTS idx_code_chunks_path ON code_chunks(file_path);

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
CREATE INDEX IF NOT EXISTS idx_matches_review ON matches(review_state);

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
