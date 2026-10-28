// PM owns one overlay per mounted project; unmount must restore the host.
export function createPmOverlay(owner) {
  let current = null;
  let released = false;
  // Overflowing compact tables are keyboard-scrollable regions; recheck on each Tab so resizes stay correct.
  const syncTableRegions = (panel) => {
    for (const region of panel.querySelectorAll('.pm-table-scroll')) {
      if (region.scrollWidth > region.clientWidth || region.scrollHeight > region.clientHeight) {
        region.tabIndex = 0; region.setAttribute('role', 'region');
        region.setAttribute('aria-label', [...region.querySelectorAll('th')].map((th) => th.textContent.trim()).join(', '));
      } else { region.tabIndex = -1; region.removeAttribute?.('role'); region.removeAttribute?.('aria-label'); }
    }
  };
  const focusable = (panel) => {
    syncTableRegions(panel);
    return [...panel.querySelectorAll('button,input,textarea,select,[tabindex]')]
      .filter((item) => item.tabIndex >= 0 && !item.disabled && !item.hidden && !item.closest?.('[hidden]'));
  };
  function close(panel = current?.panel, { force = false, restoreFocus = true } = {}) {
    if (!current || current.panel !== panel) return true;
    if (!force && current.canClose && !current.canClose()) return false;
    const closed = current; current = null;
    closed.backdrop.remove();
    for (const [element, inert] of closed.background) element.inert = inert;
    closed.onClose?.();
    if (restoreFocus) {
      const target = closed.previousFocus?.isConnected ? closed.previousFocus : owner.querySelector('[aria-current="page"]');
      target?.focus?.({ preventScroll: true });
    }
    return true;
  }
  function open(panel, { canClose, onClose, initialFocus, previousFocus = document.activeElement } = {}) {
    const returnFocus = current?.previousFocus || previousFocus;
    if (released || !owner.isConnected || !close(undefined, { restoreFocus: false })) return false;
    const backdrop = document.createElement('div'); backdrop.className = 'pm-overlay-backdrop';
    panel.setAttribute('aria-modal', 'true'); panel.setAttribute('tabindex', '-1');
    backdrop.append(panel); document.body.append(backdrop);
    const background = [...document.body.children].filter((element) => element !== backdrop).map((element) => [element, element.inert]);
    for (const [element] of background) element.inert = true;
    current = { panel, backdrop, background, previousFocus: returnFocus, canClose, onClose };
    backdrop.addEventListener('click', (event) => { if (event.target === backdrop) close(panel); });
    panel.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(panel); }
      if (event.key !== 'Tab') return;
      const items = focusable(panel), first = items[0], last = items.at(-1);
      if (!first) { event.preventDefault(); panel.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    (initialFocus && !initialFocus.disabled ? initialFocus : focusable(panel)[0] || panel).focus();
    return true;
  }
  const dispose = () => close(undefined, { force: true, restoreFocus: false });
  return { open, close, get panel() { return current?.panel; }, dispose, destroy: () => { released = true; dispose(); } };
}
