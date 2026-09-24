"use client";

import Link from "next/link";
import { useState } from "react";
import type { Finding, Job } from "@/lib/api";
import { api, plural } from "@/lib/api";
import PassagePair from "./PassagePair";

type Action = "commit" | "override" | "replace" | "cancel";
type Phase = { action: Action; state: "working" | "done" } | null;

const HEAD = {
  conflict: "Conflict found",
  review: "Needs review",
  concord: "In concord",
  duplicate: "Already in the record",
} as const;

function subline(job: Job): string {
  const docs = job.documents.length;
  const conflicts = job.conflicts.filter((f) => f.severity === "conflict").length;
  if (job.duplicate_of)
    return job.duplicate_of.match === "identical"
      ? `This file is identical to ${job.duplicate_of.filename}. A second copy cannot be added.`
      : `Every claim in this file is already stated in ${job.duplicate_of.filename}. A second copy cannot be added.`;
  if (job.verdict === "conflict")
    return `This upload contradicts ${plural(docs, "document")} in the record${
      conflicts > 1 ? `, in ${conflicts} places` : ""
    }.`;
  if (job.verdict === "review")
    return `${plural(job.conflicts.length, "passage")} may conflict with the record. Confirm before adding it.`;
  return "Nothing in this upload contradicts the record.";
}

function groupByDocument(findings: Finding[]) {
  const groups = new Map<string, { filename: string; id: string; items: Finding[] }>();
  for (const f of findings) {
    const g = groups.get(f.existing.document_id) ?? { filename: f.existing.filename, id: f.existing.document_id, items: [] };
    g.items.push(f);
    groups.set(f.existing.document_id, g);
  }
  return [...groups.values()];
}

export default function VerdictFinding({ job, onReset }: { job: Job; onReset: () => void }) {
  const [phase, setPhase] = useState<Phase>(null);
  const [overriding, setOverriding] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [committedId, setCommittedId] = useState<string | null>(null);

  const verdict = job.verdict ?? "review";
  const groups = groupByDocument(job.conflicts);
  const replaceTargets = job.documents.map((d) => d.document_id);
  const replaceLabel =
    job.documents.length === 1 ? `Replace ${job.documents[0].filename}` : `Replace ${plural(job.documents.length, "document")}`;
  const locked = phase !== null;

  async function run(action: Action) {
    setError(null);
    setPhase({ action, state: "working" });
    try {
      if (action === "cancel") {
        await api.cancel(job.upload_id);
      } else {
        const res = await api.commit({
          upload_id: job.upload_id,
          action,
          reason: action === "override" ? reason.trim() : undefined,
          replace_document_ids: action === "replace" ? replaceTargets : undefined,
        });
        setCommittedId(res.document_id);
      }
      setPhase({ action, state: "done" });
    } catch (e) {
      setPhase(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function label(action: Action, idle: string, working: string, done: string) {
    if (phase?.action !== action) return idle;
    return phase.state === "working" ? working : done;
  }

  function cls(action: Action, base: string) {
    return phase?.action === action && phase.state === "done" ? "button done" : base;
  }

  const indexOf = new Map(groups.flatMap((g) => g.items).map((f, i) => [f.id, i + 1]));
  return (
    <article className="verdict">
      <header className="verdict-head">
        <p className="verdict-filename">{job.filename}</p>
        <h1 className={`verdict-title ${verdict}`}>{HEAD[verdict]}</h1>
        <p className="verdict-sub">{subline(job)}</p>
        {!job.duplicate_of && (
          <div className="verdict-stats">
            <span>
              <span className="figure">{job.claims_checked ?? 0}</span> {job.claims_checked === 1 ? "claim" : "claims"} read
            </span>
            <span>
              <span className="figure">{job.pairs_checked ?? 0}</span> {job.pairs_checked === 1 ? "passage" : "passages"} compared
            </span>
            <span>
              <span className="figure">{job.documents_total}</span> {job.documents_total === 1 ? "document" : "documents"} in the record
            </span>
          </div>
        )}
      </header>

      {groups.map((g) => (
        <section className="doc-group" key={g.id}>
          <p className="doc-group-title">
            In <Link href={`/documents/${g.id}`}>{g.filename}</Link>
          </p>
          <ol className="findings">
            {g.items.map((f) => {
              return (
                <li key={f.id} className={`finding ${f.severity}`}>
                  <span className="finding-index">{indexOf.get(f.id)}</span>
                  <div>
                    <h2 className="finding-title">
                      {f.title}
                      {f.severity === "review" && <span className="finding-tag review">Needs review</span>}
                    </h2>
                    <PassagePair finding={f} />
                    {f.explanation && <p className="finding-explanation">{f.explanation}</p>}
                    {!f.explanation && f.severity === "review" && (
                      <p className="finding-explanation">
                        {f.note ??
                          (f.numeric
                            ? `The passages state different figures: ${f.numeric[0]} and ${f.numeric[1]}.`
                            : "The contradiction check was not decisive for this pair.")}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </section>
      ))}

      <footer className="decision">
        {job.duplicate_of ? (
          <div className="actions">
            <Link className="button" href={`/documents/${job.duplicate_of.id}`}>
              Open {job.duplicate_of.filename}
            </Link>
            <button className={cls("cancel", "button quiet")} disabled={locked} onClick={() => run("cancel")}>
              {label("cancel", "Cancel upload", "Cancelling", "Cancelled")}
            </button>
          </div>
        ) : (
          <div className="actions">
            {verdict === "concord" && (
              <button className={cls("commit", "button primary")} disabled={locked} onClick={() => run("commit")}>
                {label("commit", "Add to record", "Adding", "Added to record")}
              </button>
            )}
            {verdict === "review" && (
              <button className={cls("commit", "button primary")} disabled={locked} onClick={() => run("commit")}>
                {label("commit", "Confirm and add to record", "Adding", "Added to record")}
              </button>
            )}
            {verdict !== "concord" && replaceTargets.length > 0 && (
              <button className={cls("replace", "button")} disabled={locked} onClick={() => run("replace")}>
                {label("replace", replaceLabel, "Replacing", "Replaced")}
              </button>
            )}
            {verdict === "conflict" && !overriding && (
              <button className={cls("override", "button")} disabled={locked} onClick={() => setOverriding(true)}>
                {label("override", "Upload anyway", "Uploading", "Uploaded with override")}
              </button>
            )}
            <button className={cls("cancel", "button quiet")} disabled={locked} onClick={() => run("cancel")}>
              {label("cancel", "Cancel upload", "Cancelling", "Cancelled")}
            </button>
          </div>
        )}

        {overriding && (
          <div className="override">
            <label className="field" htmlFor="reason">
              Reason for adding a conflicting file. This is recorded with the upload.
            </label>
            <textarea
              id="reason"
              rows={3}
              value={reason}
              disabled={locked}
              onChange={(e) => setReason(e.target.value)}
              placeholder="The new figure supersedes the record from 1 October."
            />
            <div className="actions">
              <button
                className={cls("override", "button primary")}
                disabled={locked || !reason.trim()}
                onClick={() => run("override")}
              >
                {label("override", "Upload anyway", "Uploading", "Uploaded with override")}
              </button>
              {!locked && (
                <button className="button quiet" onClick={() => setOverriding(false)}>
                  Keep blocked
                </button>
              )}
            </div>
          </div>
        )}

        {error && <p className="error-text">{error}</p>}

        {phase?.state === "done" && (
          <p className="outcome">
            {phase.action === "cancel" && "Nothing was added to the record. "}
            {phase.action === "replace" && `${plural(replaceTargets.length, "document")} removed; the upload is now in the record. `}
            {phase.action === "override" && "The upload is in the record and the override is logged. "}
            {phase.action === "commit" && "The upload is now part of the record. "}
            {committedId && <Link href={`/documents/${committedId}`}>Open the document</Link>}
            {committedId && " or "}
            <button className="button quiet" style={{ padding: 0 }} onClick={onReset}>
              check another file
            </button>
            .
          </p>
        )}
      </footer>
    </article>
  );
}
