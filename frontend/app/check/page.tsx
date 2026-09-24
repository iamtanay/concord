"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import AuditProgress from "@/components/AuditProgress";
import BackendNotice from "@/components/BackendNotice";
import DropZone from "@/components/DropZone";
import VerdictFinding from "@/components/VerdictFinding";
import { api, BackendDown, type Job } from "@/lib/api";

export default function CheckPage() {
  return (
    <Suspense>
      <Check />
    </Suspense>
  );
}

function Check() {
  const params = useSearchParams();
  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState<string | null>(params.get("job"));
  const [down, setDown] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    const poll = async () => {
      try {
        const j = await api.job(jobId);
        if (!alive) return;
        setDown(false);
        setJob(j);
        if (j.status === "running") timer.current = setTimeout(poll, 700);
      } catch (e) {
        if (!alive) return;
        if (e instanceof BackendDown) {
          setDown(true);
          timer.current = setTimeout(poll, 3000);
        } else {
          setError(e instanceof Error ? e.message : String(e));
          setJobId(null);
          window.history.replaceState(null, "", "/check");
        }
      }
    };
    poll();
    return () => {
      alive = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [jobId]);

  const start = useCallback(async (file: File) => {
    setError(null);
    setStarting(file.name);
    try {
      const { job_id } = await api.startCheck(file);
      window.history.replaceState(null, "", `/check?job=${job_id}`);
      setJob(null);
      setJobId(job_id);
      setDown(false);
    } catch (e) {
      if (e instanceof BackendDown) setDown(true);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(null);
    }
  }, []);

  const reset = useCallback(() => {
    window.history.replaceState(null, "", "/check");
    setJob(null);
    setJobId(null);
    setError(null);
  }, []);

  if (down) return <BackendNotice />;

  if (job?.status === "done") return <VerdictFinding key={job.job_id} job={job} onReset={reset} />;

  if (job?.status === "error")
    return (
      <article className="verdict">
        <header className="verdict-head">
          <p className="verdict-filename">{job.filename}</p>
          <h1 className="verdict-title error">The audit could not finish</h1>
          <p className="verdict-sub">Nothing has been added to the record. {job.error}</p>
        </header>
        <div className="actions">
          <button className="button primary" onClick={reset}>
            Check another file
          </button>
        </div>
      </article>
    );

  if (job || jobId || starting)
    return (
      <AuditProgress
        job={
          job ?? {
            job_id: "",
            upload_id: "",
            filename: starting ?? "",
            status: "running",
            stage: "queued",
            progress: 0,
            message: "",
            documents_total: 0,
            verdict: null,
            conflicts: [],
            documents: [],
            duplicate_of: null,
          }
        }
      />
    );

  return (
    <>
      <h1 className="page-title">Check an upload</h1>
      <p className="lede">
        Concord reads each claim in the file, finds the passages in the record it could contradict, and blocks the
        upload if any of them conflict. Nothing is added until you decide.
      </p>
      <DropZone onFile={start} />
      {error && <p className="error-text">{error}</p>}
    </>
  );
}
