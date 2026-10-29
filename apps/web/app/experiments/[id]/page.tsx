import { OwnerShell } from "@/components/owner-shell";
import { ExperimentCockpit } from "@/components/experiment-cockpit";
export default async function ExperimentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <OwnerShell><ExperimentCockpit key={id} experimentId={id} /></OwnerShell>;
}
