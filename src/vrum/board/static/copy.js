// A button that puts the text of an element on the clipboard. Where the browser will not allow it
// (a page that is not secure), the text is selected instead, so that a person can copy it. Used where
// the board shows something to be pasted somewhere else: the endpoint, a command, a new key.

import { announce, h, put } from './dom.js';
import { icon } from './icons.js';

export function copyButton(source, label) {
  const button = h('button', { type: 'button', class: 'small copy-button', 'aria-label': label }, 'Copy');
  let timer = null;
  const said = (text, spoken) => {
    put(button, text === 'Copy' ? 'Copy' : [icon('check', { size: 14 }), text]);
    announce(spoken);
    clearTimeout(timer);
    timer = setTimeout(() => put(button, 'Copy'), 2000);
  };
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(source.textContent);
      said('Copied', 'Copied.');
    } catch {
      const range = document.createRange();
      range.selectNodeContents(source);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      said('Selected', 'Selected. Press Control C, or Command C, to copy it.');
    }
  });
  return button;
}
