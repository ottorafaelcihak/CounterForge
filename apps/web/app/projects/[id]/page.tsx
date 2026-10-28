import { OwnerShell } from "@/components/owner-shell";
import { ProjectSetup } from "@/components/project-setup";
export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <OwnerShell><ProjectSetup projectId={id} /></OwnerShell>;
}
