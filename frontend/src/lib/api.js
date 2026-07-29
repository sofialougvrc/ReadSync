const API_BASE = import.meta.env.VITE_API_BASE || ''

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options)
  if (!response.ok) {
    let detail = response.statusText
    try {
      const data = await response.json()
      detail = data.detail || JSON.stringify(data)
    } catch {
      detail = await response.text()
    }
    throw new Error(detail)
  }
  return response.json()
}

export const api = {
  health: () => request('/api/health'),
  dashboard: () => request('/api/dashboard'),
  papers: (q = '') => request(`/api/papers${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  paper: (id) => request(`/api/papers/${id}`),
  extractPaper: (id) => request(`/api/papers/${id}/extract`, { method: 'POST' }),
  matchPaper: (id) => request(`/api/papers/${id}/match`, { method: 'POST' }),
  addNote: (id, body) => request(`/api/papers/${id}/notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  }),
  ingestUrl: (text, sourceType = 'arxiv') => request('/api/ingest/url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, source_type: sourceType }),
  }),
  ingestBibtex: (bibtex) => request('/api/ingest/bibtex', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bibtex }),
  }),
  ingestPdf: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/ingest/pdf', { method: 'POST', body: form })
  },
  repos: () => request('/api/repos'),
  addRepo: (path) => request('/api/repos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  }),
  uploadRepoZip: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/repos/upload', { method: 'POST', body: form })
  },
  reindexRepo: (id) => request(`/api/repos/${id}/index`, { method: 'POST' }),
  reindexAll: () => request('/api/repos/index-all', { method: 'POST' }),
  chunks: (q = '', repoId = '') => {
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    if (repoId) params.set('repo_id', repoId)
    return request(`/api/chunks?${params.toString()}`)
  },
  matches: (state = '') => request(`/api/matches${state ? `?review_state=${state}` : ''}`),
  reviewMatch: (id, review_state) => request(`/api/matches/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ review_state }),
  }),
  runMatch: (paperId = '') => request(`/api/match/run${paperId ? `?paper_id=${paperId}` : ''}`, { method: 'POST' }),
  processAll: () => request('/api/process/all', { method: 'POST' }),
  graph: (confidence = 0) => request(`/api/graph?confidence=${confidence}`),
  timeline: () => request('/api/timeline'),
  settings: () => request('/api/settings'),
  checkLlm: () => request('/api/settings/check-llm', { method: 'POST' }),
  updateSettings: (payload) => request('/api/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
  clearDatabase: () => request('/api/database', { method: 'DELETE' }),
}
