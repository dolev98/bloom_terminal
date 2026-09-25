import { create } from 'zustand'

export type Toast = { id: number; text: string; kind?: 'ok' | 'err' | 'info' }
type ToastState = { toasts: Toast[]; push: (text: string, kind?: Toast['kind']) => void; remove: (id: number) => void }
let seq = 1
export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  push: (text, kind = 'info') => {
    const id = seq++
    set({ toasts: [...get().toasts, { id, text, kind }] })
    setTimeout(() => get().remove(id), kind === 'err' ? 8000 : 4000)
  },
  remove: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}))

export const toast = (text: string, kind?: Toast['kind']) => useToasts.getState().push(text, kind)
