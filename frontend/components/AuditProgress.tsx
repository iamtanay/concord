import type { Job } from "@/lib/api";
import { plural } from "@/lib/api";

const STAGES: Record<string, string> = {
  queued: "Waiting for the auditor",
  loading: "Loading models",
  reading: "Reading the upload",
  retrieving: "Finding the passages each claim could conflict with",
  checking: "Checking each claim against its candidate passages",
  adjudicating: "Confirming flagged passages",
};

export default function AuditProgress({ job }: { job: Job }) {
  const title =
    job.stage === "loading" || job.stage === "queued"
      ? "Preparing the audit"
      : `Auditing against ${plural(job.documents_total, "document")}`;
  const detail = job.stage === "adjudicating" ? job.message : STAGES[job.stage] ?? job.message;
  return (
    <section className="audit" aria-live="polite">
      <p className="verdict-filename">{job.filename}</p>
      <h1 className="audit-title">{title}</h1>
      <div className="audit-track" role="progressbar" aria-valuenow={Math.round(job.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
        <div className="audit-bar" style={{ width: `${Math.max(2, job.progress * 100)}%` }} />
      </div>
      <div className="audit-meta">
        <span>{detail}</span>
        <span className="figure">{Math.round(job.progress * 100)}%</span>
      </div>
    </section>
  );
}
