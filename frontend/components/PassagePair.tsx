import Link from "next/link";
import type { Finding } from "@/lib/api";
import { citation } from "@/lib/api";
import ConfidenceFigure from "./ConfidenceFigure";

function Highlight({ text, mark }: { text: string; mark?: string }) {
  if (!mark) return <>{text}</>;
  const i = text.toLowerCase().indexOf(mark.toLowerCase());
  if (i < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, i)}
      <mark>{text.slice(i, i + mark.length)}</mark>
      {text.slice(i + mark.length)}
    </>
  );
}

export default function PassagePair({ finding }: { finding: Finding }) {
  const ex = finding.existing;
  return (
    <>
      <div className="pair">
        <div className="passage theirs">
          <p className="passage-label">In the record</p>
          <blockquote>
            <Highlight text={ex.raw_text} mark={finding.numeric?.[0]} />
          </blockquote>
        </div>
        <div className="passage ours">
          <p className="passage-label">This upload</p>
          <blockquote>
            <Highlight text={finding.new_claim} mark={finding.numeric?.[1]} />
          </blockquote>
        </div>
      </div>
      <div className="provenance">
        <span>
          Source:{" "}
          <Link href={`/documents/${ex.document_id}#${ex.chunk_id}`}>
            {citation(ex.filename, ex.section_path, ex.page)}
          </Link>
        </span>
        <ConfidenceFigure value={finding.confidence} />
      </div>
    </>
  );
}
