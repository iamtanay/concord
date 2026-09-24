"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import BackendNotice from "@/components/BackendNotice";
import { api, BackendDown, formatDate, formatSection, type Chunk, type DocumentDetail } from "@/lib/api";

function sections(chunks: Chunk[]) {
  const out: { path: string; chunks: Chunk[] }[] = [];
  for (const c of chunks) {
    const last = out[out.length - 1];
    if (last && last.path === c.section_path) last.chunks.push(c);
    else out.push({ path: c.section_path, chunks: [c] });
  }
  return out;
}

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [down, setDown] = useState(false);
  const [missing, setMissing] = useState(false);
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    api
      .document(id)
      .then((d) => {
        setDoc(d);
        setTarget(window.location.hash.slice(1) || null);
      })
      .catch((e) => (e instanceof BackendDown ? setDown(true) : setMissing(true)));
  }, [id]);

  useEffect(() => {
    if (target) requestAnimationFrame(() => document.getElementById(target)?.scrollIntoView({ block: "center" }));
  }, [target]);

  if (down) return <BackendNotice />;
  if (missing)
    return (
      <>
        <Link className="back" href="/">
          The record
        </Link>
        <h1 className="page-title">This document is no longer in the record</h1>
        <p className="lede">It may have been replaced or removed.</p>
      </>
    );
  if (!doc) return <p className="soft">Opening the document…</p>;

  return (
    <article>
      <Link className="back" href="/">
        The record
      </Link>
      <h1 className="page-title">{doc.filename}</h1>
      <div className="doc-meta">
        <span>
          Version <span className="figure">{doc.version}</span>
        </span>
        <span>Indexed {formatDate(doc.uploaded_at)}</span>
        <span>
          <span className="figure">{doc.chunks.length}</span> claims
        </span>
        <span className="figure" title="SHA-256">
          {doc.sha256.slice(0, 12)}
        </span>
      </div>
      {sections(doc.chunks).map((s, i) => (
        <section className="doc-section" key={`${s.path}-${i}`}>
          {s.path && <h2>{s.path.split(" > ").map(formatSection).join("  /  ")}</h2>}
          {s.chunks.map((c) => (
            <div id={c.id} key={c.id} className={`claim${target === c.id ? " target" : ""}`}>
              <span className="figure">{c.page ? `p.${c.page}` : c.ordinal + 1}</span>
              <p>{c.raw_text}</p>
            </div>
          ))}
        </section>
      ))}
    </article>
  );
}
