"use client";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type PointerEvent } from "react";
import { assetURL } from "@/lib/client-api";
type MaskSource = { id: string; mask_asset_id: string };
export type MaskCanvasHandle = { png: () => Promise<Blob>; dirty: () => boolean };
function image(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => { const value = new Image(); value.onload = () => resolve(value); value.onerror = () => reject(new Error("An image could not be loaded. Sign in again or retry.")); value.src = url; });
}
function canvas(width: number, height: number) { const result = document.createElement("canvas"); result.width = width; result.height = height; return result; }
export const MaskCanvas = forwardRef<MaskCanvasHandle, {
  sourceAsset: string; masks: MaskSource[]; selectedId: string; overlay: boolean;
  onDirty: (value: boolean) => void; disabled: boolean;
}>(function MaskCanvas({ sourceAsset, masks, selectedId, overlay, onDirty, disabled }, ref) {
  const display = useRef<HTMLCanvasElement>(null), base = useRef<HTMLCanvasElement | null>(null), others = useRef<HTMLCanvasElement | null>(null), layer = useRef<HTMLCanvasElement | null>(null);
  const baseline = useRef<Uint8Array>(new Uint8Array()), strokeCheckpointed = useRef(false), lastPoint = useRef<{x:number; y:number} | null>(null);
  const pixels = useRef<Uint8Array>(new Uint8Array()), tinted = useRef<ImageData | null>(null), undo = useRef<Uint8Array[]>([]);
  const dimensions = useRef({ width: 0, height: 0 }), changed = useRef(false), drawing = useRef(false);
  const [size, setSize] = useState({ width: 0, height: 0 }), [ready, setReady] = useState(false), [error, setError] = useState("");
  const [mode, setMode] = useState<"paint" | "erase">("paint"), [radius, setRadius] = useState(8), [undoCount, setUndoCount] = useState(0);
  const [rectangle, setRectangle] = useState({ left: 0, top: 0, width: 40, height: 40 });
  const overlayRef = useRef(overlay);
  const maskKey = masks.map(mask => mask.id + ":" + mask.mask_asset_id).join(",");
  const render = useCallback(() => {
    const ctx = display.current?.getContext("2d");
    if (!ctx || !base.current) return;
    ctx.clearRect(0, 0, dimensions.current.width, dimensions.current.height);
    ctx.drawImage(base.current, 0, 0);
    if (overlayRef.current) {
      if (others.current) ctx.drawImage(others.current, 0, 0);
      if (layer.current) ctx.drawImage(layer.current, 0, 0);
    }
  }, []);
  useEffect(() => { overlayRef.current = overlay; render(); }, [overlay, render]);
  useEffect(() => {
    let active = true;
    changed.current = false; undo.current = [];
    async function load() {
      const source = await image(assetURL(sourceAsset));
      if (!active) return;
      const w = source.naturalWidth, h = source.naturalHeight;
      if (!w || !h || w * h > 16_000_000) throw new Error("This source exceeds the editor size limit.");
      dimensions.current = { width: w, height: h }; setSize({ width: w, height: h });
      const background = canvas(w, h); background.getContext("2d")!.drawImage(source, 0, 0);
      const other = canvas(w, h), selected = canvas(w, h), current = new Uint8Array(w * h);
      const scratch = canvas(w, h), scratchCtx = scratch.getContext("2d", { willReadFrequently: true })!;
      const otherCtx = other.getContext("2d")!;
      const entries = maskKey ? maskKey.split(",").map(entry => entry.split(":")) : [];
      for (const [id, asset] of entries) {
        const bitmap = await image(assetURL(asset)); if (!active) return;
        if (bitmap.naturalWidth !== w || bitmap.naturalHeight !== h) throw new Error("A mask has incorrect dimensions.");
        scratchCtx.clearRect(0, 0, w, h); scratchCtx.drawImage(bitmap, 0, 0);
        const raw = scratchCtx.getImageData(0, 0, w, h);
        for (let i = 0; i < current.length; i++) {
          const white = raw.data[i * 4] === 255 && raw.data[i * 4 + 3] === 255;
          if (id === selectedId) current[i] = white ? 255 : 0;
          raw.data[i * 4] = id === selectedId ? 0 : 101;
          raw.data[i * 4 + 1] = id === selectedId ? 185 : 104;
          raw.data[i * 4 + 2] = id === selectedId ? 219 : 204;
          raw.data[i * 4 + 3] = white ? 120 : 0;
        }
        if (id !== selectedId) { scratchCtx.putImageData(raw, 0, 0); otherCtx.drawImage(scratch, 0, 0); }
      }
      if (!active) return;
      base.current = background; others.current = other; layer.current = selected; pixels.current = current; baseline.current = current.slice();
      tinted.current = selected.getContext("2d")!.createImageData(w, h);
      for (let i = 0; i < current.length; i++) {
        tinted.current.data[i * 4] = 0; tinted.current.data[i * 4 + 1] = 185;
        tinted.current.data[i * 4 + 2] = 219; tinted.current.data[i * 4 + 3] = current[i] ? 155 : 0;
      }
      selected.getContext("2d")!.putImageData(tinted.current, 0, 0);
      if (display.current) { display.current.width = w; display.current.height = h; }
      setReady(true); render();
    }
    load().catch(e => { if (active) setError(e instanceof Error ? e.message : "The canvas could not be loaded."); });
    return () => { active = false; drawing.current = false; };
  }, [sourceAsset, maskKey, selectedId, onDirty, render]);
  function checkpoint() {
    const maximum = Math.max(1, Math.min(8, Math.floor(32 * 1024 * 1024 / Math.max(1, pixels.current.length))));
    undo.current.push(pixels.current.slice()); if (undo.current.length > maximum) undo.current.shift();
    setUndoCount(undo.current.length);
  }
  function mark() { changed.current = true; onDirty(true); }
  function syncDirty() { changed.current = pixels.current.some((value, i) => value !== baseline.current[i]); onDirty(changed.current); }
  function updateRegion(x0: number, y0: number, x1: number, y1: number, predicate?: (x: number, y: number) => boolean) {
    const { width, height } = dimensions.current, data = tinted.current;
    if (!data || !layer.current) return;
    const left = Math.max(0, x0), top = Math.max(0, y0), right = Math.min(width, x1), bottom = Math.min(height, y1);
    let edited = false;
    for (let y = top; y < bottom; y++) for (let x = left; x < right; x++) {
      if (predicate && !predicate(x, y)) continue;
      const i = y * width + x, value = mode === "paint" ? 255 : 0;
      if (pixels.current[i] === value) continue;
      if (!strokeCheckpointed.current) { checkpoint(); strokeCheckpointed.current = true; }
      pixels.current[i] = value; edited = true;
      data.data[i * 4 + 3] = mode === "paint" ? 155 : 0;
    }
    if (!edited) return;
    layer.current.getContext("2d")!.putImageData(data, 0, 0, left, top, Math.max(0, right - left), Math.max(0, bottom - top));
    mark(); render();
  }
  function brush(event: PointerEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect(), { width, height } = dimensions.current;
    const x = Math.max(0, Math.min(width - 1, Math.floor((event.clientX - rect.left) * width / rect.width)));
    const y = Math.max(0, Math.min(height - 1, Math.floor((event.clientY - rect.top) * height / rect.height)));
    const previous = lastPoint.current || {x, y}; lastPoint.current = {x, y};
    const dx = x - previous.x, dy = y - previous.y, squared = dx * dx + dy * dy;
    updateRegion(Math.min(x, previous.x) - radius, Math.min(y, previous.y) - radius,
      Math.max(x, previous.x) + radius + 1, Math.max(y, previous.y) + radius + 1, (a, b) => {
        const projection = squared ? Math.max(0, Math.min(1, ((a - previous.x) * dx + (b - previous.y) * dy) / squared)) : 0;
        return (a - previous.x - projection * dx) ** 2 + (b - previous.y - projection * dy) ** 2 <= radius ** 2;
      });
  }

  function restore() {
    const previous = undo.current.pop(); if (!previous || !tinted.current || !layer.current) return;
    pixels.current = previous;
    for (let i = 0; i < previous.length; i++) tinted.current.data[i * 4 + 3] = previous[i] ? 155 : 0;
    layer.current.getContext("2d")!.putImageData(tinted.current, 0, 0); setUndoCount(undo.current.length); syncDirty(); render();
  }
  useImperativeHandle(ref, () => ({
    dirty: () => changed.current,
    png: async () => {
      const { width, height } = dimensions.current;
      if (!width || !height || !pixels.current.some(value => value > 0)) throw new Error("Paint a nonempty mask before saving.");
      const output = canvas(width, height), ctx = output.getContext("2d")!, raw = ctx.createImageData(width, height);
      for (let i = 0; i < pixels.current.length; i++) { const v = pixels.current[i]; raw.data[i * 4] = v; raw.data[i * 4 + 1] = v; raw.data[i * 4 + 2] = v; raw.data[i * 4 + 3] = 255; }
      ctx.putImageData(raw, 0, 0);
      return new Promise<Blob>((resolve, reject) => output.toBlob(value => value ? resolve(value) : reject(new Error("The mask could not be exported.")), "image/png"));
    },
  }));
  const editable = ready && !disabled && overlay;
  return <div className="mask-editor"><div className="canvas-frame"><canvas ref={display} role="img" aria-label="Source image with instance mask overlays" tabIndex={0} style={{ maxWidth: size.height ? "calc(65vh * " + (size.width / size.height) + ")" : "100%" }}
    onPointerDown={event => { if (!editable) return; event.preventDefault(); strokeCheckpointed.current = false; lastPoint.current = null; drawing.current = true; event.currentTarget.setPointerCapture(event.pointerId); brush(event); }}
    onPointerMove={event => { if (drawing.current && editable) brush(event); }}
    onPointerUp={event => { drawing.current = false; lastPoint.current = null; syncDirty(); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
    onPointerCancel={() => { drawing.current = false; lastPoint.current = null; syncDirty(); }} /></div>
    {!ready && !error && <p role="status">Loading source and masks…</p>}{error && <p className="error" role="alert">{error}</p>}
    <div className="canvas-caption"><span>{size.width} × {size.height} source pixels</span><span><i className="legend-swatch selected" /> Selected mask <i className="legend-swatch neighbor" /> Other masks</span></div>
    <fieldset className="brush-toolbar" disabled={!editable}><legend>Correct this instance</legend><div className="tool-buttons"><button type="button" aria-pressed={mode === "paint"} className={mode === "paint" ? "primary" : "secondary"} onClick={() => setMode("paint")}>Paint</button><button type="button" aria-pressed={mode === "erase"} className={mode === "erase" ? "primary" : "secondary"} onClick={() => setMode("erase")}>Erase</button><button type="button" className="secondary" onClick={restore} disabled={!undoCount}>Undo stroke</button></div><label>Brush radius<input type="range" min={1} max={64} value={radius} onChange={e => setRadius(Number(e.target.value))} />{radius} px</label></fieldset>
    <fieldset className="rectangle-tools" disabled={!editable}><legend>Keyboard-friendly rectangle</legend><p className="muted">Use source pixel coordinates. Paint fills the rectangle; Erase clears it.</p><div className="rectangle-fields">{(["left", "top", "width", "height"] as const).map(key => <label key={key}>{key[0].toUpperCase() + key.slice(1)}<input aria-label={"Rectangle " + key} type="number" min={key === "width" || key === "height" ? 1 : 0} max={key === "left" || key === "width" ? size.width : size.height} value={rectangle[key]} onChange={e => setRectangle(current => ({ ...current, [key]: Number(e.target.value) }))} /></label>)}<button type="button" className="secondary" onClick={() => { const r = rectangle; if (!Object.values(r).every(Number.isInteger) || r.left < 0 || r.top < 0 || r.width < 1 || r.height < 1 || r.left + r.width > size.width || r.top + r.height > size.height) { setError("The rectangle must fit inside the source image."); return; } setError(""); strokeCheckpointed.current = false; updateRegion(r.left, r.top, r.left + r.width, r.top + r.height); syncDirty(); }}>Apply rectangle</button></div></fieldset>
  </div>;
});
