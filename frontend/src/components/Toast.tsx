"use client";

import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, AlertTriangle } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

type Toast = { id: number; kind: "ok" | "err"; text: string };
type ToastApi = { ok: (text: string) => void; err: (text: string) => void };

const ToastContext = createContext<ToastApi | null>(null);
let counter = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = ++counter;
    setToasts((prev) => [...prev, { id, kind, text }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4500);
  }, []);

  const api = useMemo<ToastApi>(() => ({ ok: (t) => push("ok", t), err: (t) => push("err", t) }), [push]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
        <AnimatePresence>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, x: 24 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 24 }}
              transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
              className="pointer-events-auto flex items-start gap-3 rounded-xl border border-white/10 bg-ink-800/90 p-3.5 text-sm backdrop-blur-md"
            >
              {t.kind === "ok" ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-sage" aria-hidden />
              ) : (
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-clay" aria-hidden />
              )}
              <span>{t.text}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}