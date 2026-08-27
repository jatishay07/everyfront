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
import { SimulatedBadge } from "./SimulatedBadge";

const PATTERN = /\*\*([^*]+)\*\*|`([^`]+)`/g;

/**
 * `agent_core.pipeline._filing_detail` prefixes every `filer.file` event's
 * detail with a code-built `[SIMULATED] …` or `[LIVE] …` token, deliberately
 * FIRST in the string so it survives truncation. Rendering it as literal
 * square brackets in the middle of prose would bury the one word in the
 * activity feed a judge most needs to catch at 4x playback speed, so it is
 * lifted out of the text and rendered as the same badge the fronts panel and
 * the filings list use — one visual vocabulary for one fact.
 *
 * Events written before that backend fix carry no token; they simply render
 * as prose, which is correct. Send mode for those filings is still on screen,
 * read from `filings[].simulated` by the Filings section and the fronts panel
 * rather than scraped out of a narration string.
 */
const MODE_TOKEN = /^\[(SIMULATED|LIVE)\]\s*/;

export function RichText({ text }: { text: string }) {
  const nodes: React.ReactNode[] = [];

  const modeMatch = MODE_TOKEN.exec(text);
  if (modeMatch) {
    nodes.push(
      <SimulatedBadge key="mode" simulated={modeMatch[1] === "SIMULATED"} className="mr-1.5 align-middle" />
    );
    text = text.slice(modeMatch[0].length);
  }

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
