import { useToasts } from '../lib/toast'

export default function Toasts() {
  const toasts = useToasts((s) => s.toasts)
  const remove = useToasts((s) => s.remove)
  if (!toasts.length) return null
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind ?? ''}`} onClick={() => remove(t.id)}>
          {t.text}
        </div>
      ))}
    </div>
  )
}
