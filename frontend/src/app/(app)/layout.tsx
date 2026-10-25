"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { Shell } from "@/components/Shell";
import { Skeleton } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function AppLayout({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  if (!ready || !user) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-8">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  return <Shell>{children}</Shell>;
}