import { CaseDetailView } from "@/components/CaseDetailView";

export default function CaseDetailPage({ params }: { params: { id: string } }) {
  return <CaseDetailView caseId={params.id} />;
}
