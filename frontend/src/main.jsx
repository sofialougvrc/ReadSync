import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import * as d3 from 'd3'
import { api } from './lib/api.js'
import './styles.css'

const windows = [
  { id: 'dashboard', icon: '▣', title: 'Dashboard' },
  { id: 'ingest', icon: '⇩', title: 'Ingest' },
  { id: 'papers', icon: '▤', title: 'Papers' },
  { id: 'code', icon: '⌘', title: 'Code Explorer' },
  { id: 'graph', icon: '◎', title: 'Graph' },
  { id: 'timeline', icon: '◷', title: 'Timeline' },
  { id: 'settings', icon: '⚙', title: 'Settings' },
]

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
  render() {
    if (this.state.error) {
      return (
        <div className="desktop">
          <section className="win-window">
            <div className="win-titlebar">
              <div className="title-left"><span className="title-icon">!</span><strong>ReadSync recovered from a display error</strong></div>
            </div>
            <div className="win-content">
              <PanelStatus error text={this.state.error.message || 'A frontend panel failed to render.'} />
              <button onClick={() => window.location.reload()}>Reload ReadSync</button>
            </div>
          </section>
        </div>
      )
    }
    return this.props.children
  }
}

function useAsync(loader, deps = []) {
  const [state, setState] = useState({ loading: true, error: '', data: null })
  const refresh = async () => {
    setState(prev => ({ ...prev, loading: true, error: '' }))
    try {
      const data = await loader()
      setState({ loading: false, error: '', data })
    } catch (error) {
      setState({ loading: false, error: error.message, data: null })
    }
  }
  useEffect(() => { refresh() }, deps)
  return { ...state, refresh }
}

function WindowShell({ title, icon, children, active, onFocus }) {
  return (
    <section className={`win-window ${active ? 'is-active' : ''}`} onMouseDown={onFocus} aria-label={title}>
      <div className="win-titlebar">
        <div className="title-left"><span className="title-icon">{icon}</span><strong>{title}</strong></div>
        <div className="title-buttons"><button aria-label="Minimize">_</button><button aria-label="Maximize">□</button><button aria-label="Close">×</button></div>
      </div>
      <div className="win-menubar"><span>File</span><span>Edit</span><span>View</span><span>Tools</span><span>Help</span></div>
      <div className="win-content">{children}</div>
    </section>
  )
}

function Badge({ children, tone = 'blue' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

function Dashboard({ openWindow }) {
  const { data, loading, error, refresh } = useAsync(api.dashboard, [])
  const [status, setStatus] = useState('')
  async function processAll() {
    setStatus('Processing every paper against indexed code...')
    try {
      const result = await api.processAll()
      setStatus(`Done: ${result.processed_count} papers processed, ${result.failed_count} need attention.`)
      await refresh()
    } catch (err) {
      setStatus(err.message)
    }
  }
  if (loading) return <PanelStatus text="Loading dashboard..." />
  if (error) return <PanelStatus text={error} error />
  const stats = data.stats
  return (
    <div className="grid-layout">
      <div className="stat-grid">
        {[
          ['Papers', stats.papers],
          ['Concepts', stats.concepts],
          ['Repos', stats.repos],
          ['Code chunks', stats.chunks],
          ['Pending review', stats.pending],
          ['Accepted links', stats.accepted],
          ['Needs attention', stats.errors],
        ].map(([label, value]) => <article className="stat-card" key={label}><span>{label}</span><strong>{value}</strong></article>)}
      </div>
      <div className="panel">
        <div className="panel-head"><strong>Research Map Repair</strong><button onClick={processAll}>Process all papers</button></div>
        <p className="muted">Extracts missing paper knowledge, refreshes pending matches, and preserves accepted or rejected review decisions.</p>
        {status && <p>{status}</p>}
      </div>
      <div className="panel">
        <div className="panel-head"><strong>Review Queue</strong><button onClick={() => openWindow('graph')}>Open Graph</button></div>
        {data.pending.length ? data.pending.map(match => (
          <div className="match-line" key={match.id}>
            <Badge tone={match.classification === 'already_implemented' ? 'green' : 'yellow'}>{match.classification}</Badge>
            <div><strong>{match.idea_name}</strong><p>{match.module_path} · {Math.round(match.confidence * 100)}%</p></div>
          </div>
        )) : <p className="muted">No pending matches yet. Ingest papers, index a repo, then run matching.</p>}
      </div>
      <div className="panel">
        <div className="panel-head"><strong>Recent Activity</strong><button onClick={refresh}>Refresh</button></div>
        <div className="activity-list">{data.recent.map(item => <p key={item.id}><span>{item.kind}</span>{item.title}<em>{new Date(item.created_at).toLocaleString()}</em></p>)}</div>
      </div>
    </div>
  )
}

function Ingest() {
  const [tab, setTab] = useState('arxiv')
  const [text, setText] = useState('')
  const [bib, setBib] = useState('')
  const [status, setStatus] = useState('')
  const [drag, setDrag] = useState(false)
  async function run() {
    setStatus('Processing...')
    try {
      const result = tab === 'bibtex'
        ? await api.ingestBibtex(bib)
        : await api.ingestUrl(text, tab === 'doi' ? 'doi' : tab === 'article' ? 'article' : 'arxiv')
      const count = result.paper_ids?.length || 0
      const failed = result.errors?.length || 0
      setStatus(`${count || 'Paper'} ingested. ${failed ? `${failed} source${failed === 1 ? '' : 's'} could not be read. ` : ''}Extraction and matching are running in the backend; refresh Papers in a moment.`)
    } catch (error) {
      setStatus(error.message)
    }
  }
  async function upload(file) {
    setStatus(`Uploading ${file.name}...`)
    try {
      const result = await api.ingestPdf(file)
      setStatus(`PDF ingested as paper #${result.paper_id}. Extraction and matching are running in the backend.`)
    } catch (error) {
      setStatus(error.message)
    }
  }
  return (
    <div>
      <div className="tabs">{['arxiv', 'article', 'doi', 'pdf', 'bibtex'].map(item => <button className={tab === item ? 'active' : ''} onClick={() => setTab(item)} key={item}>{item}</button>)}</div>
      <div className="panel inset">
        {tab === 'pdf' ? (
          <label
            className={`dropzone ${drag ? 'drag' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]) }}
          >
            <input type="file" accept="application/pdf" onChange={(e) => e.target.files[0] && upload(e.target.files[0])} />
            <strong>Drag PDF here</strong>
            <span>PyMuPDF extracts text and stores raw content in SQLite.</span>
          </label>
        ) : tab === 'bibtex' ? (
          <textarea className="textarea" rows="11" value={bib} onChange={e => setBib(e.target.value)} placeholder="@article{...}" />
        ) : (
          <textarea className="textarea" rows="8" value={text} onChange={e => setText(e.target.value)} placeholder={tab === 'doi' ? '10.48550/arXiv.1706.03762' : 'Paste one URL or a list of URLs'} />
        )}
      </div>
      <div className="action-row"><button onClick={run} disabled={tab === 'pdf'}>Ingest</button><span>{status}</span></div>
    </div>
  )
}

function Papers() {
  const [query, setQuery] = useState('')
  const { data: papers = [], loading, refresh } = useAsync(() => api.papers(query), [query])
  const [selected, setSelected] = useState(null)
  const detail = useAsync(() => selected ? api.paper(selected) : Promise.resolve(null), [selected])
  return (
    <div className="split">
      <aside className="sidebar-panel">
        <input className="input" placeholder="Search papers..." value={query} onChange={e => setQuery(e.target.value)} />
        <button onClick={refresh}>Refresh</button>
        <div className="listbox">
          {loading ? <p>Loading...</p> : papers.map(paper => (
            <button className={selected === paper.id ? 'selected' : ''} key={paper.id} onClick={() => setSelected(paper.id)}>
              <strong>{paper.title}</strong><span>{paper.year || 'n.d.'} · {paper.status}</span>
            </button>
          ))}
        </div>
      </aside>
      <main className="detail-panel">
        {!selected && <PanelStatus text="Select a paper to inspect extraction, matches, notes, and metadata." />}
        {detail.loading && selected && <PanelStatus text="Loading paper..." />}
        {detail.data && <PaperDetail paper={detail.data} refresh={detail.refresh} />}
      </main>
    </div>
  )
}

function PaperDetail({ paper, refresh }) {
  const [note, setNote] = useState('')
  const [status, setStatus] = useState('')
  async function saveNote() {
    if (!note.trim()) return
    await api.addNote(paper.id, note)
    setNote('')
    refresh()
  }
  async function rerunExtraction() {
    setStatus('Re-extracting paper and rebuilding matches...')
    try {
      const result = await api.extractPaper(paper.id)
      setStatus(`Done: ${result.matching?.matches_created ?? 0} candidate matches refreshed.`)
      await refresh()
    } catch (error) {
      setStatus(error.message)
    }
  }
  async function rerunMatching() {
    setStatus('Matching this paper against indexed code...')
    try {
      const result = await api.matchPaper(paper.id)
      setStatus(`Done: ${result.matches_created ?? 0} candidate matches created for this paper.`)
      await refresh()
    } catch (error) {
      setStatus(error.message)
    }
  }
  return (
    <div className="paper-detail">
      <h2>{paper.title}</h2>
      <p className="muted">{(paper.authors || []).join(', ') || 'Unknown authors'} · {paper.year || 'n.d.'} · {paper.source_type}</p>
      <p>{paper.abstract}</p>
      <div className="action-row">
        <button onClick={rerunExtraction}>Re-run extraction + matching</button>
        <button onClick={rerunMatching}>Run matching for this paper</button>
        {status && <span>{status}</span>}
      </div>
      <Section title="Concepts">{paper.concepts.length ? paper.concepts.map(c => <PillCard key={c.id} title={c.name} meta={`${c.type_tag} · ${Math.round(c.confidence * 100)}%`} text={c.description} />) : <p className="muted">No extracted concepts yet. Re-run extraction after Ollama is running.</p>}</Section>
      <Section title="Algorithms">{paper.algorithms.length ? paper.algorithms.map(a => <PillCard key={a.id} title={a.name} meta={`${Math.round(a.confidence * 100)}%`} text={a.description} code={a.pseudocode} />) : <p className="muted">No algorithmic procedures extracted yet.</p>}</Section>
      <Section title="Patterns / Datasets / Metrics">{[...paper.code_patterns, ...paper.items].length ? [...paper.code_patterns, ...paper.items].map((item, idx) => <PillCard key={`${item.kind || 'pattern'}-${idx}`} title={item.name || item.label} meta={item.language || item.kind || 'pattern'} text={item.description || item.detail || ''} />) : <p className="muted">No datasets, metrics, limitations, citations, or code patterns extracted yet.</p>}</Section>
      <Section title="Matched Code">{paper.matches.length ? paper.matches.map(match => <MatchReview key={match.id} match={match} onDone={refresh} />) : <p className="muted">No matches yet. Index a repository in Code Explorer, then run matching for this paper.</p>}</Section>
      <Section title="Personal Notes">
        <textarea className="textarea" rows="4" value={note} onChange={e => setNote(e.target.value)} placeholder="Private note about this paper..." />
        <button onClick={saveNote}>Save note</button>
        {paper.notes.map(item => <p className="note" key={item.id}>{item.body}</p>)}
      </Section>
    </div>
  )
}

function CodeExplorer() {
  const repos = useAsync(api.repos, [])
  const [path, setPath] = useState('')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const chunks = useAsync(() => api.chunks(query), [query])
  const [selected, setSelected] = useState(null)
  async function connectRepo({ matchAfter = true } = {}) {
    if (!path.trim()) {
      setStatus('Paste a GitHub repository link or a local project folder path first.')
      return
    }
    setBusy(true)
    setStatus('Connecting repository. If this is a GitHub URL, ReadSync will clone it first...')
    try {
      const result = await api.addRepo(path)
      setPath('')
      let message = `Repository indexed: ${result.chunks ?? 0} function-level chunks created.`
      if (matchAfter) {
        setStatus(`${message} Matching papers against this code now...`)
        const matchResult = await api.processAll()
        message = `${message} Matched ${matchResult.processed_count ?? 0} papers; ${matchResult.failed_count ?? 0} need attention.`
      }
      setStatus(message)
      await repos.refresh()
      await chunks.refresh()
    } catch (error) {
      setStatus(error.message)
    } finally {
      setBusy(false)
    }
  }
  async function reindexAndMatch() {
    setBusy(true)
    setStatus('Reindexing repositories...')
    try {
      const result = await api.reindexAll()
      setStatus(`Reindex complete: ${result.chunk_count ?? 0} chunks. Refreshing matches now...`)
      const matchResult = await api.processAll()
      setStatus(`Reindex complete: ${result.chunk_count ?? 0} chunks across ${result.repo_count ?? 0} repos. Matched ${matchResult.processed_count ?? 0} papers.`)
      await repos.refresh()
      await chunks.refresh()
    } catch (error) {
      setStatus(error.message)
    } finally {
      setBusy(false)
    }
  }
  async function uploadZip(file) {
    if (!file) return
    setBusy(true)
    setStatus(`Uploading ${file.name} and indexing code...`)
    try {
      const result = await api.uploadRepoZip(file)
      setStatus(`ZIP indexed: ${result.chunks ?? 0} chunks. Matching papers now...`)
      const matchResult = await api.processAll()
      setStatus(`ZIP indexed: ${result.chunks ?? 0} chunks. Matched ${matchResult.processed_count ?? 0} papers; ${matchResult.failed_count ?? 0} need attention.`)
      await repos.refresh()
      await chunks.refresh()
    } catch (error) {
      setStatus(error.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="split">
      <aside className="sidebar-panel">
        <div className="helper-card">
          <strong>Connect code in one step</strong>
          <p>Paste a GitHub repo URL or a local project folder. ReadSync will index functions/classes, then refresh paper-to-code matches automatically.</p>
        </div>
        <input className="input" value={path} onChange={e => setPath(e.target.value)} placeholder="/path/to/local/repo or https://github.com/user/repo" />
        <button onClick={() => connectRepo({ matchAfter: true })} disabled={busy}>Connect repo + match papers</button>
        <button onClick={() => connectRepo({ matchAfter: false })} disabled={busy}>Index only</button>
        <button onClick={reindexAndMatch} disabled={busy}>Reindex all + rematch</button>
        <label className="zip-upload">
          <input type="file" accept=".zip,application/zip" disabled={busy} onChange={e => uploadZip(e.target.files?.[0])} />
          Upload code ZIP + match
        </label>
        {status && <p className="muted">{status}</p>}
        <div className="listbox">{(repos.data || []).map(repo => <button key={repo.id}><strong>{repo.name}</strong><span>{repo.status} · {repo.chunk_count} chunks</span></button>)}</div>
      </aside>
      <main className="detail-panel">
        <input className="input" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search code chunks..." />
        <div className="code-grid">
          <div className="listbox tall">{(chunks.data || []).map(chunk => <button key={chunk.id} onClick={() => setSelected(chunk)}><strong>{chunk.signature}</strong><span>{chunk.module_path}:{chunk.start_line}</span></button>)}</div>
          <pre className="source-view" tabIndex="0">{selected ? `${selected.module_path}:${selected.start_line}-${selected.end_line}\n\n${selected.body}` : 'Select a function-level chunk.'}</pre>
        </div>
      </main>
    </div>
  )
}

function GraphPage() {
  const [confidence, setConfidence] = useState(0.2)
  const { data, loading, error } = useAsync(() => api.graph(confidence), [confidence])
  const ref = useRef(null)
  useEffect(() => {
    if (!data || !ref.current) return
    const width = ref.current.clientWidth || 760
    const height = 520
    const svg = d3.select(ref.current).selectAll('svg').data([null]).join('svg').attr('width', width).attr('height', height)
    svg.selectAll('*').remove()
    const nodes = data.nodes.map(d => ({ ...d }))
    const links = data.links.map(d => ({ ...d }))
    const color = d => d.type === 'paper' ? '#4ea1ff' : d.type === 'concept' ? '#ffb14e' : '#5ce09b'
    const simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(95))
      .force('charge', d3.forceManyBody().strength(-240))
      .force('center', d3.forceCenter(width / 2, height / 2))
    const link = svg.append('g').attr('stroke', '#8f8f8f').attr('stroke-opacity', 0.55).selectAll('line').data(links).join('line').attr('stroke-width', d => 1 + d.confidence * 2)
    const node = svg.append('g').selectAll('g').data(nodes).join('g').call(d3.drag()
      .on('start', (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y })
      .on('end', (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null }))
    node.append('circle').attr('r', d => d.type === 'paper' ? 13 : 9).attr('fill', color).attr('stroke', '#111').attr('stroke-width', 1.5)
    node.append('title').text(d => `${d.type}: ${d.label}`)
    node.append('text').text(d => d.label.slice(0, 22)).attr('x', 13).attr('y', 4).attr('fill', '#fff').attr('font-size', 13)
    simulation.on('tick', () => {
      link.attr('x1', d => d.source.x).attr('y1', d => d.source.y).attr('x2', d => d.target.x).attr('y2', d => d.target.y)
      node.attr('transform', d => `translate(${d.x},${d.y})`)
    })
    return () => simulation.stop()
  }, [data])
  return (
    <div>
      <div className="graph-toolbar"><label>Confidence ≥ {Math.round(confidence * 100)}%<input type="range" min="0" max="1" step="0.05" value={confidence} onChange={e => setConfidence(Number(e.target.value))} /></label></div>
      {loading && <PanelStatus text="Loading graph..." />}
      {error && <PanelStatus text={error} error />}
      <div className="graph-canvas" ref={ref} aria-label="D3 force-directed graph" />
      <details className="panel"><summary>Accessible graph text view</summary>{data?.links.map((link, idx) => <p key={idx}>{link.source} → {link.target} · {link.type} · {Math.round(link.confidence * 100)}%</p>)}</details>
    </div>
  )
}

function Timeline() {
  const { data, loading, error, refresh } = useAsync(api.timeline, [])
  const items = Array.isArray(data) ? data : []
  if (loading) return <PanelStatus text="Loading timeline..." />
  if (error) return <PanelStatus text={error} error />
  return (
    <div>
      <div className="action-row">
        <button onClick={refresh}>Refresh timeline</button>
        <span>{items.length} recorded events</span>
      </div>
      <div className="timeline">
        {items.length ? items.map(item => {
          const date = item.created_at ? new Date(item.created_at) : null
          const dateLabel = date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : 'Unknown time'
          return (
            <article key={item.id}>
              <strong>{item.kind || 'event'}</strong>
              <h3>{item.title || 'Untitled activity'}</h3>
              <p>{item.detail || 'No additional detail recorded.'}</p>
              <span>{dateLabel}</span>
            </article>
          )
        }) : <PanelStatus text="No timeline events yet. Ingest a paper, index a repo, or review a match to create activity." />}
      </div>
    </div>
  )
}

function Settings() {
  const { data = {}, refresh } = useAsync(api.settings, [])
  const [form, setForm] = useState({})
  useEffect(() => setForm(data || {}), [data])
  async function save() {
    await api.updateSettings({ ollama_endpoint: form.ollama_endpoint, ollama_model: form.ollama_model })
    refresh()
  }
  async function clear() {
    if (confirm('Clear the local ReadSync database?')) await api.clearDatabase()
  }
  const [status, setStatus] = useState('')
  async function reindexEverything() {
    setStatus('Reindexing all repositories...')
    try {
      const result = await api.reindexAll()
      setStatus(`Reindex complete: ${result.chunk_count ?? 0} chunks across ${result.repo_count ?? 0} repos.`)
    } catch (error) {
      setStatus(error.message)
    }
  }
  return (
    <div className="settings-grid">
      <label>Ollama endpoint<input className="input" value={form.ollama_endpoint || ''} onChange={e => setForm({ ...form, ollama_endpoint: e.target.value })} /></label>
      <label>Ollama model<input className="input" value={form.ollama_model || ''} onChange={e => setForm({ ...form, ollama_model: e.target.value })} /></label>
      <div className="action-row"><button onClick={save}>Save settings</button><button onClick={reindexEverything}>Trigger full reindex</button><button className="danger" onClick={clear}>Clear database</button>{status && <span>{status}</span>}</div>
      <div className="panel"><strong>Local-first design</strong><p>ReadSync stores papers, code chunks, embeddings, and review decisions in SQLite on this machine. Ollama runs locally. No SaaS account is required.</p></div>
    </div>
  )
}

function Section({ title, children }) {
  return <section className="detail-section"><h3>{title}</h3><div>{children}</div></section>
}

function PillCard({ title, meta, text, code }) {
  return <article className="pill-card"><strong>{title}</strong><span>{meta}</span><p>{text}</p>{code && <pre>{code}</pre>}</article>
}

function MatchReview({ match, onDone }) {
  async function review(state) {
    await api.reviewMatch(match.id, state)
    onDone?.()
  }
  return (
    <article className="match-card">
      <div><Badge tone={match.classification === 'already_implemented' ? 'green' : 'yellow'}>{match.classification}</Badge><strong>{match.idea_name}</strong></div>
      <p>{match.reason}</p>
      <code>{match.repo_name}/{match.module_path}:{match.start_line}</code>
      <div className="action-row"><button onClick={() => review('accepted')}>Accept</button><button onClick={() => review('rejected')}>Reject</button><button onClick={() => review('pending')}>Reset</button></div>
    </article>
  )
}

function PanelStatus({ text, error = false }) {
  return <div className={`panel-status ${error ? 'error' : ''}`}>{text}</div>
}

function App() {
  const [active, setActive] = useState('dashboard')
  const [health, setHealth] = useState(null)
  useEffect(() => { api.health().then(setHealth).catch(error => setHealth({ ok: false, error: error.message })) }, [])
  const activeWindow = windows.find(w => w.id === active)
  const page = useMemo(() => ({
    dashboard: <Dashboard openWindow={setActive} />,
    ingest: <Ingest />,
    papers: <Papers />,
    code: <CodeExplorer />,
    graph: <GraphPage />,
    timeline: <Timeline />,
    settings: <Settings />,
  })[active], [active])
  return (
    <div className="desktop">
      <aside className="desktop-icons" aria-label="ReadSync navigation">
        {windows.map(item => <button className={active === item.id ? 'selected' : ''} key={item.id} onClick={() => setActive(item.id)}><span>{item.icon}</span>{item.title}</button>)}
      </aside>
      <WindowShell title={`ReadSync — ${activeWindow.title}`} icon={activeWindow.icon} active onFocus={() => setActive(active)}>
        {page}
      </WindowShell>
      <footer className="taskbar"><button className="start">ReadSync</button><span className="task">{activeWindow.icon} {activeWindow.title}</span><span className={health?.ok ? 'clock ok' : 'clock'}>{health?.ok ? 'Backend Online' : 'Backend Offline'}</span></footer>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<ErrorBoundary><App /></ErrorBoundary>)
