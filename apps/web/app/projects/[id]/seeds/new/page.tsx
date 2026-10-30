import { OwnerShell } from "@/components/owner-shell";
import { ProjectSetup } from "@/components/project-setup";
export default async function NewSeedPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <OwnerShell><ProjectSetup key={id} projectId={id} section="seed" /></OwnerShell>;
}
