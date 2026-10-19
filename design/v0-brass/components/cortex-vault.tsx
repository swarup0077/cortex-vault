'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, Check, Clipboard, Copy, FileArchive, FileImage, FileText, FileType, FolderOpen, Keyboard, Moon, MoreHorizontal, Search, ShieldCheck, Sun, Upload, X, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'

type FileTypeKey = 'screenshot' | 'pdf' | 'text' | 'photo' | 'doc' | 'slides'
type Result = { rank: number; name: string; type: FileTypeKey; date: string; where?: string; snippet?: string; foundBy: [string, number][]; textReadBy: string; size: string; image?: string; boxes?: { left: number; top: number; width: number; height: number }[] }

const results: Result[] = [
  { rank: 1, name: 'Screenshot 2026-10-12 093415.png', type: 'screenshot', date: '12 Oct 2026', snippet: 'There were problems installing some updates … Error [[0x80070005]]: Access is denied.', foundBy: [['exact words', 1], ['meaning', 2]], textReadBy: 'OCR', size: '1920×1080 · 214 KB', image: 'Windows Update error dialog', boxes: [{ left: 19.2, top: 56.7, width: 37.4, height: 6.7 }] },
  { rank: 2, name: 'IT_helpdesk_guide.pdf', type: 'pdf', date: '4 Sep 2026', where: 'page 4 of 12', snippet: 'If the installer stops with [[0x80070005]], run it as administrator and check folder permissions.', foundBy: [['meaning', 1], ['exact words', 2]], textReadBy: 'PDF text', size: '380 KB' },
  { rank: 3, name: 'setup_log.txt', type: 'text', date: '3 Oct 2026', snippet: '[ERROR] CopyFile failed with [[0x80070005]] (access denied)', foundBy: [['exact words', 3]], textReadBy: 'plain text', size: '18 KB' },
  { rank: 4, name: 'IMG_2041.jpg', type: 'photo', date: '2 Sep 2026', image: 'laptop screen showing an error box', foundBy: [['picture', 1]], textReadBy: 'none', size: '4032×3024 · 3.1 MB' },
  { rank: 5, name: 'support_ticket_2231.docx', type: 'doc', date: '19 Aug 2026', snippet: 'User cannot save to the shared drive: [[access denied]] since Monday.', foundBy: [['meaning', 4]], textReadBy: 'document text', size: '24 KB' },
  { rank: 6, name: 'IT_onboarding.pptx', type: 'slides', date: '1 Aug 2026', where: 'slide 7 of 18', snippet: 'Common errors and what they mean: [[access]], disk full, network.', foundBy: [['meaning', 5]], textReadBy: 'slide text', size: '2.1 MB' },
]
const typeMeta: Record<FileTypeKey, { label: string; icon: typeof FileText; tone: string }> = { screenshot: { label: 'Screenshot', icon: FileImage, tone: 'text-[#9e5c3b] bg-[#f7e4d8]' }, pdf: { label: 'PDF', icon: FileType, tone: 'text-[#a23d32] bg-[#f5d8d1]' }, text: { label: 'Text', icon: FileText, tone: 'text-[#44636a] bg-[#dfecee]' }, photo: { label: 'Photo', icon: FileImage, tone: 'text-[#6e6041] bg-[#eee7d1]' }, doc: { label: 'Document', icon: FileText, tone: 'text-[#4f6388] bg-[#dfe7f3]' }, slides: { label: 'Slides', icon: FileArchive, tone: 'text-[#8a6435] bg-[#f1e3c6]' } }
function highlight(text: string) { return text.split(/(\[\[.*?\]\])/g).map((part, i) => part.startsWith('[[') ? <mark key={i}>{part.slice(2, -2)}</mark> : part) }
function Mark() { return <svg className="vault-logo" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="64" height="64" rx="15" fill="#141416"/><g transform="translate(5 5) scale(0.86)" fill="none" stroke="#D4A853" strokeLinecap="round"><circle cx="28" cy="28" r="16" strokeWidth="5"/><path d="M41 41L53 53" strokeWidth="6.5"/><path d="M49.5 28H53M28 49.5V53M12.8 43.2L10.3 45.7M6.5 28H3M12.8 12.8L10.3 10.3M28 6.5V3M43.2 12.8L45.7 10.3" strokeWidth="2.6"/></g><circle cx="29" cy="29" r="3" fill="#D4A853"/></svg> }
function VaultDial({ active, compact }: { active: boolean; compact?: boolean }) { return <div className={cn('vault-dial', active && 'vault-dial-active', compact && 'vault-dial-compact')} aria-hidden="true"><div className="dial-spokes"/><div className="dial-hub"><Mark/></div><div className="dial-notch"/></div> }
function FileGlyph({ type }: { type: FileTypeKey }) { const meta = typeMeta[type]; const Icon = meta.icon; return <span className={cn('file-glyph', meta.tone)}><Icon/></span> }

export function CortexVault() {
  const [query, setQuery] = useState('error 0x80070005 access denied')
  const [activeType, setActiveType] = useState<'all' | FileTypeKey>('all')
  const [dark, setDark] = useState(false)
  const [selected, setSelected] = useState<Result | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadOutcome, setUploadOutcome] = useState(false)
  const [recentSearches, setRecentSearches] = useState<string[]>([])
  const inputRef = useRef<HTMLInputElement>(null)
  const queryWasChanged = useRef(false)
  const recentSearchesKey = 'cortex-vault-recent-searches'
  const mode = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('mode') === 'local' ? 'local' : 'demo'
  const local = mode === 'local'
  const visible = useMemo(() => activeType === 'all' ? results : results.filter((r) => r.type === activeType), [activeType])
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(recentSearchesKey)
      if (stored) setRecentSearches(JSON.parse(stored).filter((item: unknown): item is string => typeof item === 'string').slice(0, 5))
    } catch { /* Storage may be blocked. */ }
  }, [])
  const saveSearch = (value = query) => {
    const search = value.trim()
    if (search.length < 4) return
    setRecentSearches((current) => {
      const next = [search, ...current.filter((item) => item !== search)].slice(0, 5)
      try { window.localStorage.setItem(recentSearchesKey, JSON.stringify(next)) } catch { /* Storage may be blocked. */ }
      return next
    })
  }
  useEffect(() => {
    if (!queryWasChanged.current || query.trim().length < 4) return
    const timer = window.setTimeout(() => saveSearch(), 1500)
    return () => window.clearTimeout(timer)
  }, [query])
  useEffect(() => { const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); inputRef.current?.focus() }; if (e.key === 'Escape' && document.activeElement === inputRef.current) { setQuery(''); inputRef.current?.blur() } }; window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey) }, [])
  const runUpload = () => { setUploading(true); setUploadOutcome(false); window.setTimeout(() => { setUploading(false); setUploadOutcome(true) }, 1800) }
  return <div className={cn('vault-app', dark && 'vault-dark')}>
    <header className="vault-header"><div className="brand"><Mark/><div><div className="brand-name">Cortex <span>Vault</span></div><div className="brand-sub">Private {local ? 'local index' : 'demo index'}</div></div></div><div className="header-actions"><span className="offline-status"><span/> {local ? 'Offline · local only' : 'Demo server · no cloud services'}</span>{query && <VaultDial active compact/>}<button className="icon-button" onClick={() => setDark(!dark)} aria-label="Toggle theme">{dark ? <Sun/> : <Moon/>}</button></div></header>
    <main className="vault-main">
      {!query && <section className="search-hero"><div className="hero-copy"><p className="eyebrow"><ShieldCheck/> YOUR FILES, REMEMBERED</p><h1>A vault for <em>everything</em> you saved.</h1></div><div className="hero-object"><div className="object-label">{local ? 'LOCAL INDEX' : 'DEMO INDEX'} <span>•</span> READY</div><VaultDial active/><div className="object-caption"><Zap/> Three small models. One quiet vault.</div></div></section>}
      <section className="search-shell" aria-label="File search"><Search className="search-icon"/><input ref={inputRef} value={query} onChange={(e) => { queryWasChanged.current = true; setQuery(e.target.value) }} placeholder="Search your vault..." aria-label="Search your vault"/>{query && <button className="clear-search" onClick={() => setQuery('')} aria-label="Clear search"><X/></button>}<kbd><span>{typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform) ? '⌘' : 'Ctrl'}</span> K</kbd><button className="search-submit" aria-label="Search"><ArrowDown/></button></section>
      <div className="query-row"><span>{recentSearches.length ? 'Recent searches' : 'Try a search'}</span>{(recentSearches.length ? recentSearches : ['error 0x80070005', 'paper about denoising diffusion models', 'dice on a wooden table']).map((search) => <button className="query-chip" key={search} onClick={() => { queryWasChanged.current = true; setQuery(search) }}>{search}{recentSearches.length > 0 && <i aria-label={`Remove ${search}`} onClick={(e) => { e.stopPropagation(); const next = recentSearches.filter((item) => item !== search); setRecentSearches(next); try { window.localStorage.setItem(recentSearchesKey, JSON.stringify(next)) } catch { /* Storage may be blocked. */ } }}>×</i>}</button>)}{recentSearches.length > 0 && <button className="clear-recent" onClick={() => { setRecentSearches([]); try { window.localStorage.removeItem(recentSearchesKey) } catch { /* Storage may be blocked. */ } }}>Clear</button>}</div>
      <div className="content-grid"><aside className="archive-rail"><div className="rail-heading"><span>THE ARCHIVE</span><span className="count">52 files</span></div><div className="rail-section"><div className="rail-label">FILTER BY TYPE</div><button className={cn('filter-button', activeType === 'all' && 'active')} onClick={() => setActiveType('all')}><ArchiveIcon/> All files <span>52</span></button>{(Object.keys(typeMeta) as FileTypeKey[]).map((type) => <button className={cn('filter-button', activeType === type && 'active')} key={type} onClick={() => setActiveType(type)}><FileGlyph type={type}/> {typeMeta[type].label} <span>{type === 'pdf' ? 12 : type === 'photo' ? 9 : type === 'screenshot' ? 8 : type === 'text' ? 11 : type === 'doc' ? 7 : 5}</span></button>)}</div><div className="rail-note"><div className="note-title"><ShieldCheck/> PRIVATE BY DESIGN</div><p>{local ? 'Indexing runs on your device. Nothing leaves this device.' : 'Files are read on this server by three small models. Your uploads are deleted after 30 minutes.'}</p></div><button className="upload-button" onClick={runUpload}><Upload/> {uploading ? 'Reading files…' : 'Add files'}</button>{uploading && <div className="upload-progress"><div><span>Files still being read</span><strong>67%</strong></div><div className="progress-track"><i/></div><p>OCR · embeddings · picture index</p></div>}{uploadOutcome && <div className="upload-outcomes"><p><Check/> report.pdf — added</p><p><Check/> notes.txt — already in your uploads</p><p>photo.webp — not a supported type</p><p>scan.pdf — couldn&apos;t be read</p></div>}</aside>
        <section className="results-area" aria-live="polite"><div className="results-toolbar"><div><span className="result-count">{visible.length ? '14' : '0'} <span>matches</span></span><span className="result-time">in 0.21s</span></div></div>{visible.length ? <div className="result-list">{visible.map((result) => <article className="result-card" key={result.name} onClick={() => { saveSearch(); setSelected(result) }} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter') { saveSearch(); setSelected(result) } }}><div className="rank">{String(result.rank).padStart(2, '0')}</div><FileGlyph type={result.type}/><div className="result-body"><div className="result-title"><h2>{result.name}</h2><span>{result.date}</span></div>{result.snippet && <p className="snippet">{highlight(result.snippet)}</p>}{result.image && <div className="image-match"><div className="image-placeholder"><FileImage/><span>{result.image}</span></div><span className="match-caption">{result.foundBy.some(([m]) => m === 'picture') ? 'Picture match' : 'Matched text'}</span></div>}<div className="result-meta"><span>{result.where || result.textReadBy}</span><span>{result.size}</span><span className="found-label">Found by</span>{result.foundBy.map(([method, rank]) => <span className={cn('method', method.replace(' ', '-'))} key={method}><i/> {method} <b>#{rank}</b></span>)}</div></div><button className="more-button" onClick={(e) => { e.stopPropagation(); setSelected(result) }} aria-label={`Actions for ${result.name}`}><MoreHorizontal/></button></article>)}</div> : <div className="empty-state"><div className="empty-icon"><Search/></div><h2>No results.</h2><p>Try a broader search or clear your filters.</p><button onClick={() => { setQuery(''); setActiveType('all') }}>Clear search</button></div>}</section></div>
    </main><footer className="vault-footer"><span><span className="footer-dot"/> {local ? 'Nothing leaves this device.' : 'The demo is busy, try again in a few minutes'}</span><span>Last indexed just now</span><span className="footer-shortcuts"><Keyboard/> <b>↑↓</b> move <b>↵</b> open <b>esc</b> clear</span></footer>
    {selected && <div className="detail-backdrop" onClick={() => setSelected(null)}><div className="detail-drawer" onClick={(e) => e.stopPropagation()}><button className="drawer-close" onClick={() => setSelected(null)} aria-label="Close"><X/></button><div className="drawer-kicker">RESULT {String(selected.rank).padStart(2, '0')} / {typeMeta[selected.type].label.toUpperCase()}</div>{selected.image && <div className="drawer-picture"><FileImage/><span>{selected.image}</span>{selected.boxes?.map((box, i) => <i key={i} style={{ left: `${box.left}%`, top: `${box.top}%`, width: `${box.width}%`, height: `${box.height}%` }}/>)}</div>}<FileGlyph type={selected.type}/><h2>{selected.name}</h2><p className="drawer-date">{selected.date} · {selected.size}</p><div className="drawer-actions"><button><FolderOpen/> Open</button><button><ArrowDown/> Download</button><button><Clipboard/> Copy text</button><button><Copy/> Copy link</button></div><div className="drawer-section"><span>FOUND BY</span>{selected.foundBy.map(([method, rank]) => <div className="drawer-method" key={method}><span>{method}</span><b>rank {rank}</b><Check/></div>)}</div><div className="drawer-section"><span>TEXT READ BY</span><p className="drawer-detail">{selected.textReadBy}</p></div>{selected.snippet && <div className="drawer-section"><span>EXTRACTED TEXT</span><p className="drawer-snippet">{highlight(selected.snippet)}</p></div>}</div></div>}
  </div>
}
function ArchiveIcon() { return <FileArchive/> }
