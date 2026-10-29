import { OwnerShell } from "@/components/owner-shell";
import { MutationEvidence } from "@/components/mutation-evidence";

export default async function MutationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <OwnerShell><MutationEvidence key={id} mutationId={id} /></OwnerShell>;
}
