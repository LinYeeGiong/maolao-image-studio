import { RotateCcw } from "lucide-react"
import { useState } from "react"

type OptimizedImageProps = {
  src: string
  alt: string
  className?: string
  loading?: "eager" | "lazy"
  aspectRatio?: string
}

export default function OptimizedImage({
  src,
  alt,
  className = "",
  loading = "lazy",
  aspectRatio,
}: OptimizedImageProps) {
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState<"loading" | "loaded" | "error">("loading")
  const separator = src.includes("?") ? "&" : "?"
  const retrySrc = attempt ? `${src}${separator}retry=${attempt}` : src

  return (
    <span
      className={`optimized-image ${status} ${className}`.trim()}
      style={aspectRatio ? { aspectRatio } : undefined}
    >
      <img
        key={retrySrc}
        src={retrySrc}
        alt={alt}
        loading={loading}
        decoding="async"
        onLoad={() => setStatus("loaded")}
        onError={() => setStatus("error")}
      />
      {status === "error" && (
        <button
          type="button"
          className="image-retry"
          onClick={() => {
            setStatus("loading")
            setAttempt((value) => value + 1)
          }}
        >
          <RotateCcw size={14} /> 重新加载
        </button>
      )}
    </span>
  )
}
