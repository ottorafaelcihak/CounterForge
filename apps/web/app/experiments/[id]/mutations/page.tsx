import { OwnerShell } from "@/components/owner-shell";
import { CandidateExplorer } from "@/components/candidate-gallery";
export default async function MutationsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <OwnerShell><CandidateExplorer key={id} experimentId={id} /></OwnerShell>;
}
