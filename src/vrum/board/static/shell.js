// What the layout needs from script: the height the top bar takes, for what sticks under it (the
// connection note, the messages, a page's own sticky header). The columns themselves are CSS
// (board.css).

export function keepLayout() {
  const top = document.getElementById('top');
  // The bar sticks to the top except on a phone, where it scrolls away. What sticks under it starts
  // below where it really ends.
  const publish = () => {
    const sticks = getComputedStyle(top).position === 'sticky';
    document.documentElement.style.setProperty('--top-now', `${sticks ? Math.round(top.getBoundingClientRect().height) : 0}px`);
  };
  new ResizeObserver(publish).observe(top);
  matchMedia('(min-width: 700px)').addEventListener('change', publish);
  publish();

  // Whether the last thing the person did was a pointer or the keyboard: a focus ring is drawn only for the
  // keyboard (base.css), so clicking a menu or a button never leaves a ring behind.
  const root = document.documentElement;
  const KEYS = new Set(['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End']);
  addEventListener('pointerdown', () => { root.dataset.input = 'pointer'; }, true);
  addEventListener('keydown', (event) => { if (KEYS.has(event.key)) delete root.dataset.input; }, true);
}
