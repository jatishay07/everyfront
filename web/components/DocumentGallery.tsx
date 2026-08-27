import { formatDateTime, titleCase } from "@/lib/format";
import { isSimulated } from "@/lib/simulated";
import type { CaseDocument, Filing } from "@/lib/types";
import { SimulatedBadge } from "./SimulatedBadge";

const TYPE_ICON: Record<CaseDocument["type"], string> = {
  bill: "🧾",
  itemized_bill: "📋",
  denial_letter: "✉️",
  collection_notice: "📮",
  gfe: "📄",
  income_proof: "💵",
  generated_application: "📝",
  generated_letter: "📤",
};

/**
 * A generated document IS a filing's payload — `agent_core.agents.filer`
 * writes the rendered PDF as a document and stamps the filing's id into
 * `extracted.filing_id` (verified against the live API). Matching on that id
 * is what lets the gallery say whether the PDF sitting in this tile was
 * actually transmitted, rather than showing a "generated_application" card
 * that looks, indistinguishably, like something that was mailed.
 *
 * Documents with no `filing_id` (the patient's own bill, an income proof) get
 * no badge at all — they were never a send, so there is nothing to qualify.
 */
function filingForDocument(d: CaseDocument, filings: Filing[]): Filing | undefined {
  const filingId = d.extracted?.filing_id;
  if (typeof filingId !== "string") return undefined;
  return filings.find((f) => f.filing_id === filingId);
}

export function DocumentGallery({
  documents,
  filings = [],
}: {
  documents: CaseDocument[];
  filings?: Filing[];
}) {
  if (documents.length === 0) {
    return <p className="text-sm text-ink-500">No documents uploaded yet.</p>;
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {documents.map((d) => {
        const filing = filingForDocument(d, filings);
        return (
        <div key={d.doc_id} className="flex flex-col gap-2 rounded-xl border border-ink-800 bg-ink-900/40 p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-sm font-medium text-ink-100">
              <span aria-hidden>{TYPE_ICON[d.type]}</span>
              {titleCase(d.type)}
            </span>
            {d.verified === true && (
              <span className="rounded-full bg-signal-green/15 px-2 py-0.5 text-[11px] font-semibold text-green-300">
                Verified
              </span>
            )}
            {d.verified === false && (
              <span className="rounded-full bg-signal-red/15 px-2 py-0.5 text-[11px] font-semibold text-red-300">
                Needs attention
              </span>
            )}
            {d.verified === null && (
              <span className="rounded-full bg-ink-800 px-2 py-0.5 text-[11px] font-medium text-ink-400">
                Pending review
              </span>
            )}
          </div>
          {filing && (
            <div className="flex items-center gap-2">
              <SimulatedBadge simulated={isSimulated(filing)} />
              <span className="truncate text-[11px] text-ink-500">
                {isSimulated(filing) ? "not transmitted" : "transmitted"} ·{" "}
                {filing.channel === "fax" ? "fax" : filing.channel === "mail" ? "certified mail" : filing.channel}
              </span>
            </div>
          )}
          {d.gcs_uri ? (
            <span className="truncate font-mono text-[11px] text-ink-500" title={d.gcs_uri}>
              {d.gcs_uri}
            </span>
          ) : d.raw_text ? (
            <p
              className="line-clamp-3 rounded-md bg-ink-950/60 px-2 py-1.5 font-mono text-[11px] leading-snug text-ink-500"
              title={d.raw_text}
            >
              {d.raw_text}
            </p>
          ) : null}
          <span className="text-xs text-ink-500">Uploaded {formatDateTime(d.uploaded_at)}</span>
          {d.verification_notes && (
            <p className="rounded-md border border-signal-red/25 bg-signal-red/5 px-2.5 py-2 text-xs leading-relaxed text-red-200">
              {d.verification_notes}
            </p>
          )}
        </div>
        );
      })}
    </div>
  );
}
