import { Clock3, ImagePlus, LoaderCircle, Sparkles, Upload, X } from "lucide-react"
import { type FormEvent, useEffect, useRef, useState } from "react"

const API_BASE = import.meta.env.VITE_API_URL || ""

const SIZE_OPTIONS = [
  { value: "2880x2880", label: "正方形", ratio: "1:1", pixels: "2880 × 2880", shape: "square" },
  { value: "3840x2160", label: "横屏", ratio: "16:9", pixels: "3840 × 2160", shape: "landscape" },
  { value: "2160x3840", label: "竖屏", ratio: "9:16", pixels: "2160 × 3840", shape: "portrait" },
] as const

type ImageSize = (typeof SIZE_OPTIONS)[number]["value"]

type Task = {
  task_id: string
  status: "queued" | "processing" | "succeeded" | "failed"
  progress?: string
  result?: { data?: Array<{ url?: string }> }
  error?: string
}

async function readJson(response: Response) {
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const message = body?.detail?.error?.message || body?.detail?.message || body?.detail || body?.error?.message || `请求失败 (${response.status})`
    throw new Error(typeof message === "string" ? message : JSON.stringify(message))
  }
  return body
}

function sleep(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms)
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer)
      reject(new DOMException("Aborted", "AbortError"))
    }, { once: true })
  })
}

export default function ImageGenerator() {
  const [prompt, setPrompt] = useState("")
  const [size, setSize] = useState<ImageSize>("2880x2880")
  const [count, setCount] = useState(1)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState("")
  const [task, setTask] = useState<Task | null>(null)
  const [images, setImages] = useState<string[]>([])
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const controllerRef = useRef<AbortController | null>(null)
  const startedAtRef = useRef<number | null>(null)

  useEffect(() => {
    if (!file) {
      setPreview("")
      return
    }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  useEffect(() => () => controllerRef.current?.abort(), [])

  useEffect(() => {
    if (!busy || startedAtRef.current === null) return
    const updateElapsed = () => {
      if (startedAtRef.current !== null) {
        setElapsedSeconds((performance.now() - startedAtRef.current) / 1000)
      }
    }
    updateElapsed()
    const timer = window.setInterval(updateElapsed, 100)
    return () => window.clearInterval(timer)
  }, [busy])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!prompt.trim() || busy) return

    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setBusy(true)
    setError("")
    setImages([])
    setTask(null)
    startedAtRef.current = performance.now()
    setElapsedSeconds(0)

    try {
      const form = new FormData()
      form.append("prompt", prompt.trim())
      form.append("size", size)
      form.append("n", String(count))
      if (file) form.append("image", file)

      const submitted = await readJson(await fetch(`${API_BASE}/api/v1/images/tasks`, {
        method: "POST",
        body: form,
        signal: controller.signal,
      }))
      let current: Task = { task_id: submitted.task_id, status: submitted.status || "queued" }
      setTask(current)

      while (!controller.signal.aborted) {
        if (current.status === "succeeded") {
          const delivered = current.result?.data || []
          setImages(delivered.map((item, index) => item.url?.startsWith("http")
            ? item.url
            : `${API_BASE}/api/v1/images/tasks/${encodeURIComponent(current.task_id)}/content/${index}`))
          return
        }
        if (current.status === "failed") throw new Error(current.error || "图片生成失败")
        await sleep(2500, controller.signal)
        current = await readJson(await fetch(
          `${API_BASE}/api/v1/images/tasks/${encodeURIComponent(current.task_id)}`,
          { signal: controller.signal },
        ))
        setTask(current)
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return
      setError(caught instanceof Error ? caught.message : "发生未知错误")
    } finally {
      if (!controller.signal.aborted) {
        if (startedAtRef.current !== null) {
          setElapsedSeconds((performance.now() - startedAtRef.current) / 1000)
        }
        setBusy(false)
      }
    }
  }

  return (
    <main className="page-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />
      <section className="hero">
        <div className="eyebrow"><Sparkles size={15} /> Maolao Image Studio</div>
        <h1>把你的想象，变成<span>超清画面</span></h1>
        <p>输入描述即可生成；上传参考图时，会自动切换到图片编辑模式。</p>
      </section>

      <section className="studio-card">
        <form onSubmit={handleSubmit}>
          <div className="field-head">
            <label htmlFor="prompt">画面描述</label>
            <span>模型 · gpt-image-2-4k</span>
          </div>
          <textarea id="prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)}
            placeholder="例如：一只穿宇航服的橘猫站在月球边缘，远处是蓝色地球，电影级光影，超精细……"
            rows={6} maxLength={4000} disabled={busy} />
          <div className="counter">{prompt.length} / 4000</div>

          <div className="field-head size-head">
            <label>画面比例</label>
            <span>4K 输出尺寸</span>
          </div>
          <div className="size-options">
            {SIZE_OPTIONS.map((option) => (
              <label className={`size-option ${size === option.value ? "selected" : ""}`} key={option.value}>
                <input
                  type="radio"
                  name="size"
                  value={option.value}
                  checked={size === option.value}
                  onChange={() => setSize(option.value)}
                  disabled={busy}
                />
                <span className={`ratio-shape ${option.shape}`} />
                <span className="size-copy">
                  <strong>{option.label} {option.ratio}</strong>
                  <small>{option.pixels}</small>
                </span>
              </label>
            ))}
          </div>

          <div className="field-head quantity-head">
            <label htmlFor="count">生成数量</label>
            <span>最多 128 张，以实际上游交付数量为准</span>
          </div>
          <div className="quantity-control">
            <button type="button" onClick={() => setCount((value) => Math.max(1, value - 1))} disabled={busy || count <= 1} aria-label="减少生成数量">−</button>
            <input
              id="count"
              type="number"
              min="1"
              max="128"
              value={count}
              onChange={(event) => setCount(Math.min(128, Math.max(1, Number(event.target.value) || 1)))}
              disabled={busy}
            />
            <button type="button" onClick={() => setCount((value) => Math.min(128, value + 1))} disabled={busy || count >= 128} aria-label="增加生成数量">+</button>
            <span>张图片</span>
          </div>

          <div className="field-head reference-head">
            <label>参考图 <em>可选</em></label>
            <span>{file ? "图片编辑模式" : "文生图模式"}</span>
          </div>
          {preview ? (
            <div className="preview-box">
              <img src={preview} alt="参考图预览" />
              <div><strong>{file?.name}</strong><small>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : ""}</small></div>
              <button type="button" className="icon-button" onClick={() => setFile(null)} disabled={busy} aria-label="移除参考图"><X size={18} /></button>
            </div>
          ) : (
            <label className="drop-zone">
              <input type="file" accept="image/*" onChange={(event) => setFile(event.target.files?.[0] || null)} disabled={busy} />
              <Upload size={24} /><strong>点击选择参考图</strong><span>支持 PNG、JPG、WebP 等常见图片格式</span>
            </label>
          )}
          <button className="generate-button" disabled={!prompt.trim() || busy}>
            {busy ? <LoaderCircle className="spin" size={20} /> : <Sparkles size={20} />}
            {busy ? "正在生成，请稍候…" : "开始生成 4K 图片"}
          </button>
        </form>

        <aside className="result-panel">
          <div className="result-title">
            <span>生成结果</span>
            <div className="result-meta">
              {task && <small>{task.progress || task.status}</small>}
              {startedAtRef.current !== null && <span className="task-time"><Clock3 size={13} /> {elapsedSeconds.toFixed(1)} 秒</span>}
            </div>
          </div>
          {error && <div className="error-box">{error}</div>}
          {images.length > 0 ? (
            <div className={`result-grid ${images.length > 1 ? "multiple" : ""}`}>{images.map((url, index) => (
              <figure key={url}><img src={url} alt={`生成结果 ${index + 1}`} />
                <a href={url} download={`maolao-${task?.task_id}-${index + 1}.png`}>下载原图</a></figure>
            ))}</div>
          ) : (
            <div className={`empty-result ${busy ? "working" : ""}`}>
              {busy ? <LoaderCircle className="spin" size={38} /> : <ImagePlus size={42} />}
              <strong>{busy ? "AI 正在绘制你的画面" : "等待你的创意"}</strong>
              <span>{busy ? `任务状态：${task?.status || "提交中"}` : "生成完成后，图片会展示在这里"}</span>
            </div>
          )}
        </aside>
      </section>
      <footer>API Key 仅保存在服务端 · 图片结果默认由上游保留约 1 小时</footer>
    </main>
  )
}
