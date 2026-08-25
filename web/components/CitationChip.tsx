/**
 * Citations rendered as chips — per §4 persona 6 WO2: "a judge must be able
 * to freeze-frame the video on a citation and read it." High-contrast,
 * monospace, generous padding, never truncated.
 */
export function CitationChip({ citation }: { citation: string }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-signal-violet/35 bg-signal-violet/10 px-2.5 py-1 font-mono text-[12.5px] leading-snug text-violet-200">
      <svg viewBox="0 0 24 24" fill="none" className="h-3 w-3 shrink-0 text-signal-violet">
        <path
          d="M6 4h9l5 5v11a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <path d="M9 12h6M9 16h6M9 8h2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <span className="whitespace-normal break-words">{citation}</span>
    </span>
  );
}

export function CitationRow({ citations }: { citations: string[] }) {
  if (citations.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {citations.map((c) => (
        <CitationChip key={c} citation={c} />
      ))}
    </div>
  );
}
