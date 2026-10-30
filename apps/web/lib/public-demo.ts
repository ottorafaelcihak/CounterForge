import type { components } from "@/lib/types/api.generated";

export type DemoManifest = components["schemas"]["DemoManifest"];
export type PublicDemoAsset = components["schemas"]["PublicDemoAsset"];
export type PublicDemoFinding = components["schemas"]["PublicDemoFinding"];

const PUBLIC_ASSET_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** Same-origin public image URL for one snapshot asset ID from the manifest. */
export function publicDemoAssetURL(assetId: string): string | null {
  if (!PUBLIC_ASSET_ID.test(assetId)) return null;
  return "/api/public/demo/assets/" + assetId + "/content";
}

export function isFixturePreview(manifest: DemoManifest): boolean {
  return manifest.state === "FIXTURE_PREVIEW" || manifest.provenance === "LOCAL_FIXTURE";
}

export function demoStateLabel(manifest: DemoManifest): string {
  if (manifest.state === "FIXTURE_PREVIEW") return "Local fixture preview";
  if (manifest.state === "PUBLISHED") return "Recorded demo snapshot";
  return "Demo pending publication";
}

/** Assets paired to one public finding, in manifest order. */
export function findingAssets(manifest: DemoManifest, finding: PublicDemoFinding): PublicDemoAsset[] {
  const byId = new Map((manifest.assets ?? []).map((asset) => [asset.id, asset]));
  return (finding.asset_ids ?? []).flatMap((id) => {
    const asset = byId.get(id);
    return asset ? [asset] : [];
  });
}
