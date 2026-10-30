import { OwnerShell } from "@/components/owner-shell";
import { MaskWorkspace } from "@/components/mask-workspace";
export default async function MaskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <OwnerShell><MaskWorkspace key={id} seedId={id} /></OwnerShell>;
}
