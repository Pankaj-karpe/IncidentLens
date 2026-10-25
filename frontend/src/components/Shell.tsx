"use client";

import { Aperture, LogOut, Siren } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { cx, RolePill } from "./ui";

const NAV = [{ href: "/incidents", label: "Incidents", icon: Siren }];

export function Shell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  function signOut() {
    logout();
    router.replace("/login");
  }

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
      <aside className="sticky top-0 z-30 flex items-center justify-between border-b border-white/10 bg-ink-900/80 px-4 py-3 backdrop-blur-md lg:h-dvh lg:flex-col lg:items-stretch lg:justify-start lg:gap-10 lg:border-b-0 lg:border-r lg:px-5 lg:py-7">
        <Link href="/incidents" className="flex items-center gap-2.5">
          <Aperture className="size-6 text-bronze" aria-hidden />
          <span className="font-display text-xl tracking-tight">IncidentLens</span>
        </Link>

        <nav aria-label="Main" className="hidden lg:block">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cx(
                  "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-all duration-300 ease-expo",
                  active ? "bg-white/[0.06] text-bone" : "text-mute hover:bg-white/[0.04] hover:text-bone",
                )}
              >
                <Icon className={cx("size-4", active && "text-bronze")} aria-hidden />
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-3 lg:mt-auto lg:flex-col lg:items-stretch lg:gap-3">
          {user && (
            <div className="flex items-center gap-2 lg:rounded-xl lg:border lg:border-white/10 lg:bg-white/[0.03] lg:p-3">
              <div className="grid size-8 place-items-center rounded-full bg-ink-700 font-mono text-xs uppercase">
                {user.username.slice(0, 2)}
              </div>
              <div className="hidden min-w-0 flex-1 sm:block">
                <p className="truncate text-sm">{user.username}</p>
                <RolePill role={user.role} />
              </div>
            </div>
          )}
          <button
            onClick={signOut}
            className="inline-flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm text-mute transition-colors duration-300 hover:bg-white/[0.05] hover:text-bone"
          >
            <LogOut className="size-4" aria-hidden />
            <span className="hidden sm:inline">Sign out</span>
          </button>
        </div>
      </aside>

      <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-8 lg:py-12">{children}</main>
    </div>
  );
}