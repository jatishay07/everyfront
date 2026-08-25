/**
 * The live Reader/Auditor agents' `event.detail` strings carry light
 * markdown — `**bold**` around extracted values, `` `code` `` around raw
 * field names (verified via curl against the live API, e.g. "Gemini
 * extracted ... a billed amount of **$2,625.00**" and 'classified as
 * `income_proof`'). The mock corpus never had this since it was hand-typed
 * plain text.
 *
 * Rendering it raw would put literal asterisks on screen in the one place
 * (§4 persona 6 WO2/WO3) that most needs to be freeze-frame-clean, so this
 * does a minimal, dependency-free bold/code pass — no arbitrary HTML, no
 * markdown library, just the two constructs the pipeline actually emits.
 */
const PATTERN = /\*\*([^*]+)\*\*|`([^`]+)`/g;

export function RichText({ text }: { text: string }) {
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let key = 0;
  let match: RegExpExecArray | null;
  PATTERN.lastIndex = 0;
  while ((match = PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    if (match[1] !== undefined) {
      nodes.push(
        <strong key={key++} className="font-semibold text-ink-100">
          {match[1]}
        </strong>
      );
    } else if (match[2] !== undefined) {
      nodes.push(
        <code key={key++} className="rounded bg-ink-800 px-1 py-0.5 font-mono text-[0.92em] text-ink-200">
          {match[2]}
        </code>
      );
    }
    lastIndex = PATTERN.lastIndex;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return <>{nodes}</>;
}
