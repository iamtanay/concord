"use client";

import Link from "next/link";
import { useState } from "react";
import type { DocumentRow } from "@/lib/api";
import { api, formatDate } from "@/lib/api";

export default function RecordTable({ docs, onChange }: { docs: DocumentRow[]; onChange: () => void }) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  async function remove(id: string) {
    setRemoving(id);
    try {
      await api.deleteDocument(id);
      onChange();
    } finally {
      setRemoving(null);
      setConfirming(null);
    }
  }

  return (
    <table className="table">
      <thead>
        <tr>
          <th>Document</th>
          <th className="num hide-sm">Sections</th>
          <th className="num hide-sm">Claims</th>
          <th className="num hide-sm">Version</th>
          <th>Indexed</th>
          <th aria-label="Actions" />
        </tr>
      </thead>
      <tbody>
        {docs.map((d) => (
          <tr key={d.id}>
            <td>
              <Link className="filename" href={`/documents/${d.id}`}>
                {d.filename}
              </Link>
            </td>
            <td className="num figure hide-sm">{d.section_count}</td>
            <td className="num figure hide-sm">{d.claim_count}</td>
            <td className="num figure hide-sm">{d.version}</td>
            <td className="soft">{formatDate(d.uploaded_at)}</td>
            <td className="num">
              {confirming === d.id ? (
                <span className="actions" style={{ justifyContent: "flex-end", gap: 12 }}>
                  <button className="button quiet small" onClick={() => setConfirming(null)} disabled={removing === d.id}>
                    Keep
                  </button>
                  <button className="button small" onClick={() => remove(d.id)} disabled={removing === d.id}>
                    {removing === d.id ? "Removing" : "Remove from record"}
                  </button>
                </span>
              ) : (
                <button className="button quiet small" onClick={() => setConfirming(d.id)}>
                  Remove
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
