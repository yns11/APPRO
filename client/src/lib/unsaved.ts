import { useEffect } from "react";

/**
 * Guard of a screen holding **unsaved entries** : closing or reloading the tab asks for a confirmation (``beforeunload``),
 * and so does a click on any internal link while the entries are pending (captured before React Router navigates).
 * ``message`` is shown in the in-app confirmation only : browsers show their own text on unload.
 */
export function useUnsavedGuard(dirty: boolean, message = "Des saisies ne sont pas enregistrées. Quitter la page et les perdre ?") {
  useEffect(() => {
    if (!dirty) return;
    const onUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as HTMLElement | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      if (!window.confirm(message)) { e.preventDefault(); e.stopPropagation(); }
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => { window.removeEventListener("beforeunload", onUnload); document.removeEventListener("click", onClick, true); };
  }, [dirty, message]);
}
