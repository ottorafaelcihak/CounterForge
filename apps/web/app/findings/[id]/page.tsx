import { OwnerShell } from "@/components/owner-shell";
import { FindingDetail } from "@/components/finding-detail";
export default async function FindingPage({ params }: { params: Promise<{ id: string }> }) {
 const { id } = await params;
 return <OwnerShell><FindingDetail key={id} findingId={id} /></OwnerShell>;
}
