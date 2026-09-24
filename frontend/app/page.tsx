"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import BackendNotice from "@/components/BackendNotice";
import DropZone from "@/components/DropZone";
import RecordTable from "@/components/RecordTable";
import { api, BackendDown, type DocumentRow } from "@/lib/api";

export default function RecordPage() {
  const [docs, setDocs] = useState<DocumentRow[] | null>(null);
  const [down, setDown] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .documents()
      .then((d) => {
        setDocs(d);
        setDown(false);
      })
      .catch((e) => {
        if (e instanceof BackendDown) setDown(true);
        else setError(String(e.message ?? e));
      });
  }, []);

  useEffect(load, [load]);

  async function seed(file: File) {
    setAdding(file.name);
    setError(null);
    try {
      await api.addDocument(file);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAdding(null);
    }
  }

  if (down) return <BackendNotice />;
  if (!docs) return <p className="soft">Reading the record…</p>;

  const claims = docs.reduce((n, d) => n + d.claim_count, 0);

  return (
    <>
      <div className="record-head">
        <div>
          <h1 className="page-title">The record</h1>
          <p className="soft" style={{ margin: 0 }}>
            {docs.length ? (
              <>
                <span className="figure">{docs.length}</span> {docs.length === 1 ? "document" : "documents"},{" "}
                <span className="figure">{claims}</span> indexed claims
              </>
            ) : (
              "No documents yet"
            )}
          </p>
        </div>
        {docs.length > 0 && (
          <Link className="button primary" href="/check">
            Check an upload
          </Link>
        )}
      </div>

      {docs.length > 0 ? (
        <RecordTable docs={docs} onChange={load} />
      ) : (
        <section style={{ maxWidth: 680 }}>
          <p className="lede">
            Start by indexing your existing files. From the backend folder, run{" "}
            <code className="figure">python -m ingest ./path/to/knowledge-base</code>, or add files one at a time
            here. Files added this way skip the consistency check, so use it only to seed the record.
          </p>
          {adding ? (
            <p className="serif" style={{ fontSize: 22 }}>
              Indexing {adding}…
            </p>
          ) : (
            <DropZone onFile={seed} title="Add a file to the record" hint="It becomes part of the record without a check." />
          )}
        </section>
      )}
      {error && <p className="error-text">{error}</p>}
    </>
  );
}
