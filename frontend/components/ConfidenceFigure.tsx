export default function ConfidenceFigure({ value, label = "confidence" }: { value: number | null; label?: string }) {
  if (value === null || value === undefined) return null;
  return (
    <span className="confidence">
      {label}
      <span className="figure">{value.toFixed(2)}</span>
    </span>
  );
}
