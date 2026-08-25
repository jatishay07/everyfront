import { formatDateTime, titleCase } from "@/lib/format";
import type { CaseDocument } from "@/lib/types";

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

export function DocumentGallery({ documents }: { documents: CaseDocument[] }) {
  if (documents.length === 0) {
    return <p className="text-sm text-ink-500">No documents uploaded yet.</p>;
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {documents.map((d) => (
        <div key={d.doc_id} className="flex flex-col gap-2 rounded-xl border border-ink-800 bg-ink-900/40 p-3.5">
          <div className="flex items-center justify-between">
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
          <span className="truncate font-mono text-[11px] text-ink-500" title={d.gcs_uri}>
            {d.gcs_uri}
          </span>
          <span className="text-xs text-ink-500">Uploaded {formatDateTime(d.uploaded_at)}</span>
          {d.verification_notes && (
            <p className="rounded-md border border-signal-red/25 bg-signal-red/5 px-2.5 py-2 text-xs leading-relaxed text-red-200">
              {d.verification_notes}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
