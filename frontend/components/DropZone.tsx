"use client";

import { useState } from "react";

export const ACCEPT = ".txt,.md,.markdown,.pdf,.docx";

export default function DropZone({
  onFile,
  title = "Drop a file to check it against the record",
  hint = "PDF, Word, Markdown, or plain text. Or choose a file.",
}: {
  onFile: (file: File) => void;
  title?: string;
  hint?: string;
}) {
  const [over, setOver] = useState(false);
  return (
    <label
      className={`dropzone${over ? " over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onFile(f);
      }}
    >
      <p className="serif">{title}</p>
      <p className="soft small" style={{ margin: 0 }}>
        {hint}
      </p>
      <input
        type="file"
        accept={ACCEPT}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </label>
  );
}
