"use client";

import { motion } from "framer-motion";
import { Aperture, Lock, User } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { user, ready, login } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (ready && user) router.replace("/incidents");
  }, [ready, user, router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
      router.replace("/incidents");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-dvh lg:grid-cols-[1.15fr_1fr]">
      <section className="relative hidden flex-col justify-between border-r border-white/10 p-12 lg:flex">
        <div className="flex items-center gap-2.5">
          <Aperture className="size-6 text-bronze" aria-hidden />
          <span className="font-display text-xl tracking-tight">IncidentLens</span>
        </div>
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
          className="max-w-xl"
        >
          <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-faint">On-call, with context</p>
          <h1 className="mt-4 font-display text-6xl leading-[1.02] tracking-tight">
            Less time guessing.
            <br />
            <span className="text-bronze">More time fixing.</span>
          </h1>
          <p className="mt-6 max-w-md text-base leading-relaxed text-mute">
            Every alert arrives with metrics, logs and recent deploys already gathered, a root-cause hypothesis, and a
            postmortem draft waiting for your review.
          </p>
        </motion.div>
        <p className="font-mono text-xs text-faint">Humans decide. The AI only suggests.</p>
      </section>

      <section className="flex items-center justify-center p-6">
        <motion.form
          onSubmit={onSubmit}
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
          className="w-full max-w-sm"
        >
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <Aperture className="size-6 text-bronze" aria-hidden />
            <span className="font-display text-xl">IncidentLens</span>
          </div>
          <h2 className="font-display text-4xl tracking-tight">Sign in</h2>
          <p className="mt-2 text-sm text-mute">Accounts are created by your administrator.</p>

          <label className="mt-8 block text-xs font-medium uppercase tracking-wider text-faint" htmlFor="username">
            Username
          </label>
          <div className="relative mt-2">
            <User className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
            <input
              id="username"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-xl border border-white/10 bg-ink-850 py-3 pl-10 pr-3 text-sm transition-colors duration-300 placeholder:text-faint hover:border-white/20 focus:border-bronze/60 focus:outline-none"
              placeholder="alice"
            />
          </div>

          <label className="mt-5 block text-xs font-medium uppercase tracking-wider text-faint" htmlFor="password">
            Password
          </label>
          <div className="relative mt-2">
            <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-xl border border-white/10 bg-ink-850 py-3 pl-10 pr-3 text-sm transition-colors duration-300 placeholder:text-faint hover:border-white/20 focus:border-bronze/60 focus:outline-none"
              placeholder="••••••••••••"
            />
          </div>

          <p role="alert" aria-live="assertive" className="mt-4 min-h-5 text-sm text-clay">
            {error}
          </p>

          <Button type="submit" loading={busy} className="mt-2 w-full">
            Continue
          </Button>
        </motion.form>
      </section>
    </main>
  );
}