"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Health } from "@/lib/api";

export default function Masthead() {
  const path = usePathname();
  const [health, setHealth] = useState<Health | null | "down">(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .health()
        .then((h) => alive && setHealth(h))
        .catch(() => alive && setHealth("down"));
    tick();
    const t = setInterval(tick, 10000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  let status = "";
  if (health === "down") status = "Backend offline";
  else if (health && !health.ready) status = health.error ? "Models failed to load" : "Loading models";
  else if (health) status = `${health.documents} documents in the record`;

  return (
    <header className="masthead">
      <Link href="/" className="wordmark">
        Concord
      </Link>
      <nav className="nav">
        <Link href="/" aria-current={path === "/" ? "page" : undefined}>
          The record
        </Link>
        <Link href="/check" aria-current={path === "/check" ? "page" : undefined}>
          Check an upload
        </Link>
      </nav>
      <span className="masthead-status">{status}</span>
    </header>
  );
}
