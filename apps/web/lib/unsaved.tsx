"use client";
import { createContext, useContext, useEffect } from "react";

export const UnsavedContext = createContext<{ dirty: boolean; setDirty: (value: boolean) => void } | null>(null);
export function useUnsaved() {
  const value = useContext(UnsavedContext);
  if (!value) throw new Error("The editor requires an owner workspace.");
  return value;
}
export function confirmDiscard(dirty: boolean) {
  return !dirty || window.confirm("Discard the unsaved changes?");
}
type NavigationSurface = EventTarget & { currentEntry?: { index: number } };
function navigationSurface() { return (window as Window & { navigation?: NavigationSurface }).navigation; }
function entryIndex() { return navigationSurface()?.currentEntry?.index; }
export function useUnsavedNavigation(dirty: boolean, setDirty: (value: boolean) => void) {
  useEffect(() => {
    if (!dirty) return;
    let restoring = false, approvedTraversal = false;
    const navigation = navigationSurface();
    const position = entryIndex();
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    const link = (event: MouseEvent) => {
      const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (!(anchor instanceof HTMLAnchorElement) || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || anchor.target === "_blank" || anchor.href === window.location.href) return;
      if (!confirmDiscard(true)) { event.preventDefault(); event.stopImmediatePropagation(); }
      else { window.removeEventListener("beforeunload", beforeUnload); setDirty(false); }
    };
    const navigating = (event: Event) => {
      if ((event as Event & { navigationType?: string }).navigationType !== "traverse" || !event.cancelable) return;
      if (!confirmDiscard(true)) { event.preventDefault(); return; }
      approvedTraversal = true; window.removeEventListener("beforeunload", beforeUnload); setDirty(false);
    };
    const pop = (event: PopStateEvent) => {
      if (approvedTraversal) { approvedTraversal = false; return; }
      if (restoring) { restoring = false; event.stopImmediatePropagation(); return; }
      if (confirmDiscard(true)) { window.removeEventListener("beforeunload", beforeUnload); setDirty(false); return; }
      // Stop the router before it replaces the editor, then restore the history
      // position. Older browsers without Navigation API retain the Back fallback.
      event.stopImmediatePropagation(); restoring = true;
      const next = entryIndex();
      window.history.go(position != null && next != null && next !== position ? position - next : 1);
    };
    navigation?.addEventListener("navigate", navigating);
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", link, true);
    window.addEventListener("popstate", pop, true);
    return () => { navigation?.removeEventListener("navigate", navigating); window.removeEventListener("beforeunload", beforeUnload); document.removeEventListener("click", link, true); window.removeEventListener("popstate", pop, true); };
  }, [dirty, setDirty]);
}
