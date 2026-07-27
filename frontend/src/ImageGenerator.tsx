import {
  Check, ChevronDown, Clock3, Download, ImagePlus, Menu, MessageSquare,
  Paperclip, Pencil, Plus, Search, Send, Sparkles, Trash2, X,
} from "lucide-react"
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react"
import OptimizedImage from "./OptimizedImage"

const API_BASE = import.meta.env.VITE_API_URL || ""
const MAX_REFERENCE_IMAGES = 16
const SIZE_OPTIONS = [
  { value: "2880x2880", label: "正方形", ratio: "1:1", pixels: "2880 × 2880" },
  { value: "3840x2160", label: "横屏", ratio: "16:9", pixels: "3840 × 2160" },
  { value: "2160x3840", label: "竖屏", ratio: "9:16", pixels: "2160 × 3840" },
] as const
type ImageSize = (typeof SIZE_OPTIONS)[number]["value"]
type ImageQuality = "low" | "high"
type Provider = "maolao" | "relayrouter" | "openai"
type RouteSelection = "auto" | Provider
const ROUTE_OPTIONS: { value: RouteSelection; label: string }[] = [
  { value: "auto", label: "自动选择模型" },
  { value: "maolao", label: "Maolao" },
  { value: "relayrouter", label: "RelayRouter" },
  { value: "openai", label: "OpenAI 官方" },
]

type StudioImage = {
  id: string; kind: "reference" | "generated"; position: number; file_name: string; mime_type: string; url: string
  thumbnail_url?: string; preview_url?: string; download_url?: string
}
type Turn = {
  id: string; prompt: string; size: ImageSize; quality?: ImageQuality; n: number; status: "queued" | "processing" | "succeeded" | "partially_succeeded" | "failed" | "needs_attention"
  source_image_id?: string | null; error?: string | null; elapsed_seconds?: number | null; created_at: string; images: StudioImage[]
  provider_attempts?: { provider: Provider; position: number; status: string; error_message?: string | null }[]
}
type Conversation = { id: string; title: string; updated_at: string; turn_count: number; last_status?: string | null; turns?: Turn[] }
type ModalState =
  | { type: "delete"; conversationId: string; title: string }
  | { type: "rename" }

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`, options)
  if (response.status === 204) return undefined as T
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    throw new Error(typeof detail === "string" ? detail : detail?.message || `请求失败 (${response.status})`)
  }
  return body
}

function mediaUrl(url: string) { return url.startsWith("http") ? url : `${API_BASE}${url}` }
function imageVariant(image: StudioImage, variant: "thumbnail" | "preview" | "download") {
  const selected = variant === "thumbnail"
    ? image.thumbnail_url
    : variant === "preview"
      ? image.preview_url
      : image.download_url
  return mediaUrl(selected || image.url)
}
function aspectRatio(size: ImageSize) {
  if (size === "3840x2160") return "16 / 9"
  if (size === "2160x3840") return "9 / 16"
  return "1 / 1"
}
function formatDate(value: string) {
  const date = new Date(value)
  const today = new Date()
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })
}

export default function ImageGenerator() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [prompt, setPrompt] = useState("")
  const [size, setSize] = useState<ImageSize>("2160x3840")
  const [quality, setQuality] = useState<ImageQuality>("low")
  const [count, setCount] = useState(1)
  const [route, setRoute] = useState<RouteSelection>("auto")
  const [retryOfTurnId, setRetryOfTurnId] = useState<string | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [sourceImage, setSourceImage] = useState<StudioImage | null>(null)
  const [previews, setPreviews] = useState<string[]>([])
  const [search, setSearch] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const [modal, setModal] = useState<ModalState | null>(null)
  const [renameTitle, setRenameTitle] = useState("")
  const [modalBusy, setModalBusy] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [routeOpen, setRouteOpen] = useState(false)
  const [parametersOpen, setParametersOpen] = useState(false)
  const [now, setNow] = useState(Date.now())
  const bottomRef = useRef<HTMLDivElement>(null)
  const promptRef = useRef<HTMLTextAreaElement>(null)
  const routeMenuRef = useRef<HTMLDivElement>(null)
  const parameterMenuRef = useRef<HTMLDivElement>(null)

  const loadConversations = useCallback(async () => {
    const items = await request<Conversation[]>("/api/v1/conversations")
    setConversations(items)
    return items
  }, [])

  const loadConversation = useCallback(async (id: string, inherit = false) => {
    const item = await request<Conversation>(`/api/v1/conversations/${id}`)
    setConversation(item)
    if (inherit && item.turns?.length) {
      const last = item.turns[item.turns.length - 1]
      setSize(last.size)
      setQuality(last.quality ?? "low")
      setCount(last.n)
    }
    return item
  }, [])

  useEffect(() => {
    loadConversations().then((items) => {
      if (items[0]) setActiveId(items[0].id)
    }).catch((caught) => setError(caught.message))
  }, [loadConversations])

  useEffect(() => {
    setSourceImage(null)
    setFiles([])
    if (!activeId) { setConversation(null); return }
    loadConversation(activeId, true).catch((caught) => setError(caught.message))
  }, [activeId, loadConversation])

  useEffect(() => {
    if (!activeId) return
    const interval = window.setInterval(() => {
      loadConversation(activeId).catch(() => undefined)
      loadConversations().catch(() => undefined)
    }, 2500)
    return () => window.clearInterval(interval)
  }, [activeId, loadConversation, loadConversations])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }) }, [conversation?.turns?.length])
  useEffect(() => {
    const textarea = promptRef.current
    if (!textarea) return
    textarea.style.height = "auto"
    const maxHeight = 220
    textarea.style.height = `${Math.max(76, Math.min(textarea.scrollHeight, maxHeight))}px`
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden"
  }, [prompt])
  useEffect(() => {
    const urls = files.map((file) => URL.createObjectURL(file))
    setPreviews(urls)
    return () => urls.forEach((url) => URL.revokeObjectURL(url))
  }, [files])
  useEffect(() => {
    if (!routeOpen && !parametersOpen) return
    const closeToolbarMenus = (event: MouseEvent) => {
      if (!routeMenuRef.current?.contains(event.target as Node)) setRouteOpen(false)
      if (!parameterMenuRef.current?.contains(event.target as Node)) setParametersOpen(false)
    }
    const closeToolbarMenusOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setRouteOpen(false)
        setParametersOpen(false)
      }
    }
    document.addEventListener("mousedown", closeToolbarMenus)
    document.addEventListener("keydown", closeToolbarMenusOnEscape)
    return () => {
      document.removeEventListener("mousedown", closeToolbarMenus)
      document.removeEventListener("keydown", closeToolbarMenusOnEscape)
    }
  }, [routeOpen, parametersOpen])

  const filtered = useMemo(() => conversations.filter((item) => item.title.toLowerCase().includes(search.toLowerCase())), [conversations, search])
  const currentSize = SIZE_OPTIONS.find((option) => option.value === size)!
  const currentRoute = ROUTE_OPTIONS.find((option) => option.value === route)!

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!prompt.trim() || submitting) return
    setSubmitting(true); setError("")
    try {
      let id = activeId
      if (!id) {
        const created = await request<Conversation>("/api/v1/conversations", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "新对话" }),
        })
        id = created.id
        setActiveId(id)
      }
      const form = new FormData()
      form.append("prompt", prompt.trim()); form.append("size", size); form.append("quality", quality); form.append("n", String(count))
      form.append("route_mode", route === "auto" ? "auto" : "manual")
      if (route !== "auto") form.append("selected_provider", route)
      if (retryOfTurnId) form.append("retry_of_turn_id", retryOfTurnId)
      if (files.length) files.forEach((file) => form.append("images", file))
      if (sourceImage) form.append("source_image_id", sourceImage.id)
      await request<Turn>(`/api/v1/conversations/${id}/turns`, { method: "POST", body: form })
      setPrompt(""); setFiles([]); setSourceImage(null); setRetryOfTurnId(null)
      await Promise.all([loadConversation(id), loadConversations()])
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "提交失败")
    } finally { setSubmitting(false) }
  }

  function addReferenceFiles(selected: FileList | null) {
    const incoming = Array.from(selected || [])
    if (!incoming.length) return
    const capacity = MAX_REFERENCE_IMAGES - files.length - (sourceImage ? 1 : 0)
    if (capacity <= 0) {
      setError(`参考图最多支持 ${MAX_REFERENCE_IMAGES} 张`)
      return
    }
    if (incoming.length > capacity) {
      setError(`参考图最多支持 ${MAX_REFERENCE_IMAGES} 张，已保留前 ${capacity} 张新图片`)
    }
    setFiles([...files, ...incoming.slice(0, capacity)])
  }

  async function confirmDeleteConversation() {
    if (modal?.type !== "delete" || modalBusy) return
    setModalBusy(true)
    try {
      await request(`/api/v1/conversations/${modal.conversationId}`, { method: "DELETE" })
      const remaining = await loadConversations()
      setActiveId(remaining[0]?.id || null)
      setModal(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除失败")
    } finally {
      setModalBusy(false)
    }
  }

  function openRenameModal() {
    if (!conversation) return
    setRenameTitle(conversation.title)
    setModal({ type: "rename" })
  }

  async function submitRename(event: FormEvent) {
    event.preventDefault()
    const title = renameTitle.trim()
    if (!conversation || !title || modalBusy) return
    setModalBusy(true)
    try {
      await request(`/api/v1/conversations/${conversation.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }),
      })
      await Promise.all([loadConversation(conversation.id), loadConversations()])
      setModal(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "重命名失败")
    } finally {
      setModalBusy(false)
    }
  }

  function chooseSource(image: StudioImage, turn: Turn) {
    setSourceImage(image); setSize(turn.size); setCount(turn.n)
    window.setTimeout(() => document.querySelector<HTMLTextAreaElement>("#prompt")?.focus(), 0)
  }

  function prepareRetry(turn: Turn) {
    setPrompt(turn.prompt); setSize(turn.size); setQuality(turn.quality ?? "low"); setCount(turn.n); setRetryOfTurnId(turn.id)
    promptRef.current?.focus()
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <div className="brand"><div className="brand-mark"><Sparkles size={18} /></div><span>Maolao Studio</span><button onClick={() => setSidebarOpen(false)}><X size={18} /></button></div>
        <button className="new-chat" onClick={() => { setActiveId(null); setSidebarOpen(false) }}><Plus size={17} /> 新建对话</button>
        <label className="search-box"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索对话" /></label>
        <div className="conversation-list">
          <small>最近对话</small>
          {filtered.map((item) => <div className={`conversation-row ${activeId === item.id ? "active" : ""}`} key={item.id}>
            <button className="conversation-main" onClick={() => { setActiveId(item.id); setSidebarOpen(false) }}>
              <MessageSquare size={16} /><span><strong>{item.title}</strong><em>{item.turn_count} 轮 · {formatDate(item.updated_at)}</em></span>
            </button>
            <button className="delete-chat" onClick={() => setModal({ type: "delete", conversationId: item.id, title: item.title })} title="删除"><Trash2 size={14} /></button>
          </div>)}
        </div>
        <div className="sidebar-foot">模型：gpt-image-2-4k</div>
      </aside>
      {sidebarOpen && <button className="sidebar-mask" onClick={() => setSidebarOpen(false)} aria-label="关闭侧栏" />}

      <main className="chat-area">
        <header className="chat-header">
          <button className="mobile-menu" onClick={() => setSidebarOpen(true)}><Menu size={20} /></button>
          <div><strong>{conversation?.title || "新对话"}</strong><span>4K 图片创作与连续优化</span></div>
          {conversation && <button className="header-action" onClick={openRenameModal}><Pencil size={15} /> 重命名</button>}
        </header>

        <section className="messages">
          {!conversation?.turns?.length ? <div className="welcome">
            <div className="welcome-icon"><Sparkles size={28} /></div><h1>今天想创作什么？</h1>
            <p>描述你的画面，或上传最多 16 张参考图。生成后可选择任意结果，用自然语言继续调整。</p>
          </div> : conversation.turns.map((turn) => {
            const generated = turn.images.filter((image) => image.kind === "generated")
            const uploaded = turn.images.filter((image) => image.kind === "reference")
            const elapsed = turn.elapsed_seconds ?? Math.max(0, (now - new Date(turn.created_at).getTime()) / 1000)
            return <article className="turn" key={turn.id}>
              <div className="user-message">
                {uploaded.length > 0 && <div className={`user-reference-grid count-${Math.min(uploaded.length, 4)}`}>
                  {uploaded.map((image, index) => <OptimizedImage key={image.id} className="reference-preview" src={imageVariant(image, "thumbnail")} alt={`上传的参考图 ${index + 1}`} aspectRatio={aspectRatio(turn.size)} />)}
                </div>}
                {turn.source_image_id && <span className="context-tag"><ImagePlus size={13} /> 基于上一张图继续优化</span>}
                <p>{turn.prompt}</p><small>{SIZE_OPTIONS.find((item) => item.value === turn.size)?.ratio} / {turn.size} / {turn.n} 张</small>
              </div>
              <div className="assistant-message">
                <div className="assistant-head"><span><Sparkles size={15} /> Maolao</span><em><Clock3 size={13} /> {elapsed.toFixed(1)} 秒</em></div>
                {turn.provider_attempts?.length ? <details className="provider-attempts"><summary>线路记录</summary>{turn.provider_attempts.map((attempt) => <div key={`${attempt.provider}-${attempt.position}`}><b>{ROUTE_OPTIONS.find((option) => option.value === attempt.provider)?.label}</b><span>{attempt.status}{attempt.error_message ? `：${attempt.error_message}` : ""}</span></div>)}</details> : null}
                {turn.status === "needs_attention" ? <div className="turn-error">任务状态未知：{turn.error}。<button className="retry-turn" onClick={() => prepareRetry(turn)}>人工重试</button></div> : turn.status === "failed" ? <div className="turn-error">生成失败：{turn.error}</div> : generated.length ?
                  <div className={`image-grid count-${Math.min(generated.length, 4)}`}>{generated.map((image) => <figure key={image.id}>
                    <OptimizedImage className="generated-preview" src={imageVariant(image, "preview")} alt={`生成结果 ${image.position + 1}`} aspectRatio={aspectRatio(turn.size)} />
                    <div className="image-actions"><button onClick={() => chooseSource(image, turn)}><Check size={14} /> 以此图继续优化</button><a href={imageVariant(image, "download")} download={image.file_name}><Download size={14} /></a></div>
                  </figure>)}</div> : <div className="generating-card"><span className="loader" /><div><strong>正在生成 {turn.n} 张 4K 图片</strong><small>{turn.status === "queued" ? "任务排队中" : "AI 正在绘制，请稍候"}</small></div></div>}
                {turn.status === "partially_succeeded" && <div className="turn-partial">部分完成：{generated.length} / {turn.n} 张图片已保存。</div>}
              </div>
            </article>
          })}
          <div ref={bottomRef} />
        </section>

        <div className="composer-wrap">
          {error && <div className="global-error">{error}<button onClick={() => setError("")}><X size={14} /></button></div>}
          {retryOfTurnId && <div className="retry-notice">正在人工重试上一条状态未知任务；请选择线路后发送。</div>}
          {(sourceImage || files.length > 0) && <div className="selected-images">
            {files.length + (sourceImage ? 1 : 0) > 1 && (
              <div className="selected-images-head"><span>参考图</span><strong>已选择 {files.length + (sourceImage ? 1 : 0)} / {MAX_REFERENCE_IMAGES}</strong></div>
            )}
            <div className="selected-images-row">
              {sourceImage && <div className="selected-chip">
                <OptimizedImage src={imageVariant(sourceImage, "thumbnail")} alt="已选择生成图" loading="eager" />
                <span><strong>已选择生成图</strong><small>将在此图基础上继续优化</small></span>
                <button type="button" onClick={() => setSourceImage(null)} aria-label="移除生成图"><X size={14} /></button>
              </div>}
              {files.map((file, index) => <div className="selected-chip" key={`${file.name}-${file.lastModified}-${index}`}>
                <OptimizedImage src={previews[index]} alt={`参考图 ${index + 1}`} loading="eager" />
                <span><strong>参考图 {index + 1}</strong><small>{file.name}</small></span>
                <button type="button" onClick={() => setFiles(files.filter((_, itemIndex) => itemIndex !== index))} aria-label={`移除参考图 ${index + 1}`}><X size={14} /></button>
              </div>)}
            </div>
          </div>}
          <form className="composer" onSubmit={handleSubmit}>
            <textarea ref={promptRef} id="prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() }
            }} placeholder={sourceImage ? "告诉我还需要怎样调整…" : "描述你想生成的画面…"} rows={2} maxLength={4000} aria-describedby="composer-hint" />
            <div className="composer-toolbar">
              <div className="toolbar-left">
                <label className="tool-button" title="上传参考图"><Paperclip size={17} /><input type="file" accept="image/*" multiple onChange={(event) => { addReferenceFiles(event.target.files); event.currentTarget.value = "" }} /></label>
                <div className="route-menu" ref={routeMenuRef}>
                  <button type="button" className="route-trigger" aria-haspopup="listbox" aria-expanded={routeOpen} onClick={() => { setRouteOpen(!routeOpen); setParametersOpen(false) }}>{currentRoute.label}<ChevronDown size={14} /></button>
                  {routeOpen && <div className="toolbar-popover route-popover" role="listbox" aria-label="选择生成模型">
                    {ROUTE_OPTIONS.map((option) => <button type="button" role="option" aria-selected={route === option.value} className={route === option.value ? "selected" : ""} key={option.value} onClick={() => { setRoute(option.value); setRouteOpen(false) }}><span>{option.label}</span>{route === option.value && <Check size={14} />}</button>)}
                  </div>}
                </div>
                <div className="parameter-menu" ref={parameterMenuRef}><button type="button" className="parameter-trigger" aria-haspopup="dialog" aria-expanded={parametersOpen} onClick={() => { setParametersOpen(!parametersOpen); setRouteOpen(false) }}>{currentSize.ratio} · {count} 张 · {quality === "low" ? "低质量" : "高质量"} <ChevronDown size={14} /></button>
                  {parametersOpen && <div className="toolbar-popover parameter-popover" role="dialog" aria-label="生成参数"><strong>生成参数</strong><label>图片比例</label><div className="ratio-list">{SIZE_OPTIONS.map((option) => <button type="button" className={size === option.value ? "selected" : ""} key={option.value} onClick={() => setSize(option.value)}><span className={`ratio-icon ${option.label}`} /> <b>{option.label} {option.ratio}</b><small>{option.pixels}</small></button>)}</div><label>生成质量</label><div className="quality-list"><button type="button" className={quality === "low" ? "selected" : ""} onClick={() => setQuality("low")}><b>低质量</b><small>更快、更省</small></button><button type="button" className={quality === "high" ? "selected" : ""} onClick={() => setQuality("high")}><b>高质量</b><small>更精细</small></button></div><label>生成数量 <b>{count} / 10</b></label><input type="range" min="1" max="10" value={count} onChange={(event) => setCount(Number(event.target.value))} /></div>}
                </div>
              </div>
              <div className="toolbar-right"><span className={`prompt-count ${prompt.length >= 3600 ? "near-limit" : ""}`}>{prompt.length.toLocaleString()} / 4,000</span><button className="send-button" disabled={!prompt.trim() || submitting} aria-label="发送"><Send size={18} /></button></div>
            </div>
          </form>
          <small id="composer-hint" className="composer-note">Enter 发送，Shift + Enter 换行。新图片使用私有云存储，历史图片继续兼容。</small>
        </div>
      </main>
      {modal && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !modalBusy) setModal(null) }}>
        {modal.type === "delete" ? <section className="site-modal" role="dialog" aria-modal="true" aria-labelledby="delete-modal-title">
          <button className="modal-close" onClick={() => setModal(null)} disabled={modalBusy} aria-label="关闭"><X size={18} /></button>
          <div className="modal-icon danger"><Trash2 size={20} /></div>
          <h2 id="delete-modal-title">删除对话？</h2>
          <p>“{modal.title}”及其中所有聊天和图片记录将被永久删除，此操作不可恢复。</p>
          <div className="modal-actions"><button className="secondary" onClick={() => setModal(null)} disabled={modalBusy}>取消</button><button className="danger" onClick={confirmDeleteConversation} disabled={modalBusy}>{modalBusy ? "删除中…" : "确认删除"}</button></div>
        </section> : <form className="site-modal" role="dialog" aria-modal="true" aria-labelledby="rename-modal-title" onSubmit={submitRename}>
          <button type="button" className="modal-close" onClick={() => setModal(null)} disabled={modalBusy} aria-label="关闭"><X size={18} /></button>
          <div className="modal-icon"><Pencil size={20} /></div>
          <h2 id="rename-modal-title">重命名对话</h2>
          <p>输入一个便于识别的名称。</p>
          <input autoFocus value={renameTitle} onChange={(event) => setRenameTitle(event.target.value)} maxLength={100} placeholder="对话名称" />
          <div className="modal-actions"><button type="button" className="secondary" onClick={() => setModal(null)} disabled={modalBusy}>取消</button><button type="submit" disabled={!renameTitle.trim() || modalBusy}>{modalBusy ? "保存中…" : "保存"}</button></div>
        </form>}
      </div>}
    </div>
  )
}
