// Shared positioning, host lookup, event-driven anchor tracking, and list keyboard navigation for
// floating popovers (select.js, status-menu.js, datepicker.js, hovercard.js).

export const popoverHost = (trigger) => trigger.closest('dialog[open]') || document.body;

export function boxOf(element) {
  const box = element.getBoundingClientRect();
  return `${box.left},${box.top},${box.width},${box.height}`;
}

// Places `panel` relative to `anchor`: below when it fits (or when there is more room below than
// above), otherwise above, clamped horizontally within the viewport. With `clampToViewport` (for
// fixed-size dialogs like the calendar), falls back to the bottom of the viewport if neither side
// fits cleanly. Returns the anchor's box fingerprint.
export function placePopover(
  panel,
  anchor,
  { gap = 4, edge = 8, minWidth = 0, matchWidth = false, resetOrigin = false, clampToViewport = false } = {},
) {
  const box = anchor.getBoundingClientRect();
  const width = document.documentElement.clientWidth;
  const height = clampToViewport || resetOrigin ? window.innerHeight : document.documentElement.clientHeight;
  if (matchWidth || minWidth) panel.style.minWidth = `${Math.max(matchWidth ? Math.ceil(box.width) : 0, minWidth)}px`;
  panel.style.maxHeight = '';
  if (resetOrigin) {
    panel.style.left = '0px';
    panel.style.top = '0px';
  }
  const natural = panel.offsetHeight;
  let top;
  let placement;
  if (clampToViewport) {
    const room = Math.max(0, height - 2 * edge);
    const shown = Math.min(natural, room);
    if (natural > room) panel.style.maxHeight = `${room}px`;
    top = box.bottom + gap;
    if (top + shown > height - edge) {
      const above = box.top - gap - shown;
      top = above >= edge ? above : height - edge - shown;
    }
    placement = top >= box.bottom ? 'below' : 'above';
  } else {
    const below = height - box.bottom - gap - edge;
    const above = box.top - gap - edge;
    const under = natural <= below || below >= above;
    const room = Math.max(0, under ? below : above);
    if (natural > room) panel.style.maxHeight = `${room}px`;
    const shown = Math.min(natural, room);
    top = under ? box.bottom + gap : box.top - gap - shown;
    placement = under ? 'below' : 'above';
  }
  panel.style.top = `${Math.round(top)}px`;
  panel.style.left = `${Math.round(Math.min(Math.max(box.left, edge), Math.max(edge, width - panel.offsetWidth - edge)))}px`;
  panel.dataset.placement = placement;
  return boxOf(anchor);
}

// Event-driven anchor tracking: reacts to scroll, resize, ResizeObserver, and DOM mutations,
// coalescing bursts into at most one animation frame instead of polling 60 times a second.
export function trackAnchor(getAnchor, { ignore = null, onMove, onGone }) {
  let frame = 0;
  let stopped = false;
  const initial = getAnchor();
  let lastBox = initial && initial.isConnected ? boxOf(initial) : '';
  let observed = null;

  const check = () => {
    frame = 0;
    if (stopped) return;
    const anchor = getAnchor();
    if (!anchor || !anchor.isConnected || !anchor.getClientRects().length) {
      stop();
      onGone();
      return;
    }
    if (resizeObserver && anchor !== observed) {
      if (observed) resizeObserver.unobserve(observed);
      resizeObserver.observe(anchor);
      observed = anchor;
    }
    const next = boxOf(anchor);
    if (next !== lastBox) {
      lastBox = next;
      onMove(anchor);
    }
  };

  const schedule = () => {
    if (stopped || frame) return;
    frame = requestAnimationFrame(check);
  };

  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(schedule) : null;
  if (resizeObserver && initial && initial.isConnected) {
    resizeObserver.observe(initial);
    observed = initial;
  }
  const mutationObserver = new MutationObserver((records) => {
    if (ignore && records.every((record) => ignore.contains(record.target))) return;
    schedule();
  });
  mutationObserver.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'hidden', 'open', 'style'],
  });
  window.addEventListener('scroll', schedule, { capture: true, passive: true });
  window.addEventListener('resize', schedule, { passive: true });

  function stop() {
    if (stopped) return;
    stopped = true;
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    resizeObserver?.disconnect();
    mutationObserver.disconnect();
    window.removeEventListener('scroll', schedule, { capture: true });
    window.removeEventListener('resize', schedule);
  }

  return {
    stop,
    sync(nextAnchor) {
      const target = nextAnchor || getAnchor();
      if (target && target.isConnected) lastBox = boxOf(target);
    },
  };
}

// The first row that is not disabled starting at `from` and stepping by `step`, or -1.
export function usableIndex(rows, from, step) {
  for (let at = from; at >= 0 && at < rows.length; at += step) if (!rows[at].disabled) return at;
  return -1;
}

// Matches typed letters against `textOf(row)` starting from `state.active`; repeating the same
// letter advances to the next match. Returns the matching row index, or -1.
export function typeAheadIndex(state, key, textOf, { typingMs = 700 } = {}) {
  const now = Date.now();
  state.typed = now - state.typedAt > typingMs ? key : state.typed + key;
  state.typedAt = now;
  const typed = state.typed.toLowerCase();
  const again = [...typed].every((letter) => letter === typed[0]);
  const needle = again ? typed[0] : typed;
  const from = again ? state.active + 1 : state.active;
  const count = state.rows.length;
  for (let moved = 0; moved < count; moved += 1) {
    const at = (from + moved + count) % count;
    if (!state.rows[at].disabled && textOf(state.rows[at]).toLowerCase().startsWith(needle)) return at;
  }
  return -1;
}
