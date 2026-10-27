import type { components } from "@/lib/types/api.generated";

const health: components["schemas"]["Health"] = {
  status: "alive", api_version: 1, schema_version: "1.0.0",
};

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col justify-center gap-6 px-8 py-16">
      <p className="text-sm font-semibold uppercase tracking-widest text-teal-800">Phase 01 · Foundation</p>
      <h1 className="text-5xl font-semibold tracking-tight sm:text-7xl">CounterForge</h1>
      <p className="max-w-2xl text-xl leading-relaxed">Find the failure. Prove the weakness. Prevent the regression.</p>
      <p className="max-w-2xl text-base leading-relaxed text-slate-700">
        Shared contracts and offline fixtures are ready for development. Experiment creation,
        storage and live model integrations arrive in later phases.
      </p>
      <div className="border-t border-slate-300 pt-6 text-sm text-slate-600">
        API v{health.api_version} · Contract schema {health.schema_version} · No live experiments
      </div>
    </main>
  );
}
