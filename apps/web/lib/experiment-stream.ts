/** Read-only saved events. Counters and terminal states always come from snapshots. */
import type { components } from "./types/api.generated";
type StoredEvent = components["schemas"]["StoredExperimentEvent"];
type Frame = { id: string; data: string };
export async function* eventFrames(stream: ReadableStream<Uint8Array>, signal: AbortSignal): AsyncGenerator<Frame> {
  const reader = stream.getReader(), decoder = new TextDecoder();
  let buffer = "", id = "", data: string[] = [];
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", cancel, { once: true });
  try {
    while (!signal.aborted) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      if (buffer.length > 1024 * 1024) throw new Error("Saved event exceeds the display limit.");
      let newline: number;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, ""); buffer = buffer.slice(newline + 1);
        if (!line) {
          if (data.length) yield { id, data: data.join("\n") };
          id = ""; data = [];
        } else if (line.startsWith("id:")) id = line.slice(3).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        if (data.reduce((sum, value) => sum + value.length, 0) > 1024 * 1024) throw new Error("Saved event exceeds the display limit.");
      }
    }
  } finally { signal.removeEventListener("abort", cancel); await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>(resolve => {
    const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
    if (signal.aborted) done();
  });
}
type Options<Snapshot> = {
  experimentId: string; signal: AbortSignal;
  refresh: () => Promise<Snapshot>; isTerminal: (snapshot: Snapshot) => boolean;
  fetchStream: (after: number) => Promise<Response>; history: (after: number) => Promise<StoredEvent[]>;
  onSnapshot: (snapshot: Snapshot) => void; onEvents: (events: StoredEvent[]) => void;
  onStatus: (status: string) => void; onAuthority: (authoritative: boolean) => void;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
};
export async function observeExperiment<S>(options: Options<S>): Promise<void> {
  const { signal, experimentId, onStatus, onAuthority } = options;
  let lastId = 0, retries = 0;
  function append(records: StoredEvent[]) {
    // Never trust the frame to identify a different experiment or advance an unsafe cursor.
    const fresh = records.filter(item => Number.isSafeInteger(item.id) && item.id > lastId && item.experiment_id === experimentId && typeof item.event === "string");
    const unique = [...new Map(fresh.map(item => [item.id, item])).values()].sort((a, b) => a.id - b.id);
    if (unique.length && !signal.aborted) { lastId = unique.at(-1)!.id; options.onEvents(unique); }
  }
  async function snapshot() {
    const saved = await options.refresh();
    if (signal.aborted) return null;
    options.onSnapshot(saved); onAuthority(true);
    return saved;
  }
  async function finish() {
    // Bounded, cursor-based persisted history, including final events omitted by a closing stream.
    for (let page = 0; page < 100 && !signal.aborted; page++) {
      const previous = lastId, records = await options.history(lastId);
      if (signal.aborted) return;
      append(records);
      if (records.length < 100 || lastId === previous) { onStatus("Saved events; experiment finished."); return; }
    }
    if (!signal.aborted) onStatus("Experiment finished; timeline display limit reached. Reload for saved evidence.");
  }
  while (!signal.aborted) {
    let response: Response | null = null;
    const started = Date.now();
    try {
      onAuthority(false);
      onStatus(retries ? "Reconnecting; refreshing the saved snapshot." : "Connecting to saved events…");
      const saved = await snapshot();
      if (!saved || signal.aborted) return;
      if (options.isTerminal(saved)) { await finish(); return; }
      response = await options.fetchStream(lastId);
      if (signal.aborted) return;
      // A fresh request after each handshake is mandatory, even if a previous snapshot was terminal.
      onAuthority(false);
      const current = await snapshot();
      if (!current || signal.aborted) return;
      if (options.isTerminal(current)) { await finish(); return; }
      if (!response.body || response.headers.get("content-type")?.split(";")[0] !== "text/event-stream") throw new Error("Saved event stream is unavailable.");
      onStatus("Live saved events");
      for await (const frame of eventFrames(response.body, signal)) {
        if (signal.aborted) return;
        const record = JSON.parse(frame.data) as StoredEvent;
        if (String(record.id) !== frame.id || !Number.isSafeInteger(record.id) || record.experiment_id !== experimentId) continue;
        if (record.id <= lastId) continue;
        append([record]);
        const updated = await snapshot();
        if (!updated || signal.aborted) return;
        if (options.isTerminal(updated)) { await finish(); return; }
      }
      if (signal.aborted) return;
      throw new Error("The saved event stream was interrupted.");
    } catch (error) {
      if (signal.aborted) return;
      onAuthority(false);
      const status = error && typeof error === "object" && "status" in error ? error.status : null;
      if (status === 401 || status === 403) { onStatus("Sign in again to reconnect."); return; }
      if (Date.now() - started >= 15000) retries = 0;
      if (retries >= 5 || status === 404 || status === 422) {
        onStatus((error instanceof Error ? error.message + " " : "") + "Reload experiment to reconnect."); return;
      }
      onStatus("Reconnecting; refreshing the saved snapshot.");
      await (options.sleep || wait)(Math.min(500 * 2 ** retries++, 8000), signal);
    } finally { if (response?.body && !response.body.locked) await response.body.cancel().catch(() => {}); }
  }
}
