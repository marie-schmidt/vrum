// The keys of the page of what needs a person and of the review page (only while they are on screen). They move and open and never decide:
// a decision is final, so only a press of its button sends it, and `a` and `r` only put the focus on the buttons. They are never taken while a
// field, a select, a dialog or the quick switcher has the keyboard, and never with a modifier key (Ctrl+K and the browser's own keys are left
// alone). Pressing `?` shows the list in the shared Key button.
//
// The list:
//   j  k   the next and the previous change (its header takes the focus)
//   o      open the change's review page (on the group of routine moves, open or close it)
//   v      mark it viewed, which folds its preview (or take the mark off)
//   a  r   the focus goes to the Approve button and to the Reject button of the change, without pressing either
//   ?      show this list
//
// The review page's:
//   [  ]   (also j and k) the previous and the next change, in the order of the list
//   u      back to the list (also Escape, when nothing is open)
//   s  m   Unified or Split, and Changes or Whole plan, where the change has them
//   v      mark it viewed
//   a  r  c   the focus goes to Approve, to Reject and to the comment
//
// The group of routine moves is one stop of its own (its summary) while it is closed.

import { h } from './dom.js';

const LIST_KEYS = new Set(['j', 'k', 'o', 'v', 'a', 'r', '?']);
const REVIEW_KEYS = new Set(['j', 'k', '[', ']', 'u', 'Escape', 's', 'm', 'v', 'a', 'r', 'c', '?']);

// A control that takes typed words (or a choice by typing): the keys are the person's own there.
const typing = (target) =>
  target instanceof Element && Boolean(target.closest('textarea, select, [contenteditable=""], [contenteditable="true"], input:not([type=checkbox], [type=radio], [type=button], [type=submit])'));

// Whether something else has the keyboard: a dialog, an open hover card does not count.
const busy = (event) => typing(event.target) || Boolean(document.querySelector('dialog[open]'));

const row = (keys, words) => h('li', {}, h('span', { class: 'keys-of' }, keys.map((one) => h('kbd', {}, one))), h('span', { class: 'keys-words' }, words));

// What the Key button shows on the list: each key and what it does, and that none of them decides anything. These are in a hover card,
// where a sentence is how a control says what it does.
export function keysContent() {
  return h(
    'div',
    { class: 'needs-keys-card' },
    h('p', { class: 'hovercard-title' }, 'Keys on this page'),
    h(
      'ul',
      { class: 'plain keys-list' },
      row(['j', 'k'], 'Next and previous change'),
      row(['o'], 'Open the change\u2019s review page'),
      row(['v'], 'Mark it viewed and fold its preview'),
      row(['a', 'r'], 'Go to its Approve and Reject buttons, without pressing one'),
      row(['?'], 'Show these keys'),
    ),
    h('p', { class: 'quiet' }, 'Keys move and open. They never approve, reject or answer: only pressing a button does.'),
  );
}

// The same for the review page.
export function reviewKeysContent() {
  return h(
    'div',
    { class: 'needs-keys-card' },
    h('p', { class: 'hovercard-title' }, 'Keys on this page'),
    h(
      'ul',
      { class: 'plain keys-list' },
      row(['[', ']'], 'Previous and next change (also j and k)'),
      row(['u'], 'Back to the list (also Escape)'),
      row(['s'], 'Unified or split, for a plan or a move'),
      row(['m'], 'Changes or the whole plan, for a revised plan'),
      row(['v'], 'Mark it viewed'),
      row(['a', 'r', 'c'], 'Go to Approve, to Reject and to the comment, without pressing one'),
      row(['?'], 'Show these keys'),
    ),
    h('p', { class: 'quiet' }, 'Keys move and open. They never approve, reject or answer: only pressing a button does.'),
  );
}

// The keys of the list. `page.stops()` is where j and k stop, in the order they are drawn: [{ li, head, ...the card }]; `page.open(stop)`
// opens a card's review page. Returns what stops listening.
export function keepKeys(page) {
  const onKey = (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || !LIST_KEYS.has(event.key)) return;
    if (event.shiftKey && event.key !== '?') return;
    if (busy(event)) return;
    if (event.key === '?') {
      event.preventDefault();
      document.getElementById('needs-keys-button')?.focus();
      return;
    }
    const cards = page.stops();
    if (!cards.length) return;
    const here = cards.findIndex((card) => card.li.contains(document.activeElement));
    const current = here >= 0 ? cards[here] : null;
    const go = (at) => {
      event.preventDefault();
      cards[Math.max(0, Math.min(cards.length - 1, at))].head.focus();
    };
    if (event.key === 'j') return go(current ? here + 1 : 0);
    if (event.key === 'k') return go(current ? here - 1 : cards.length - 1);
    if (!current) return;
    event.preventDefault();
    switch (event.key) {
      case 'o':
        if (current.change) page.open(current);
        else current.toggleCollapsed();
        break;
      case 'v':
        current.toggleViewed();
        current.head.focus();
        break;
      case 'a':
        current.verdicts()[0]?.focus();
        break;
      case 'r': {
        const verdicts = current.verdicts();
        (verdicts.find((one) => one.dataset.verb === 'reject') || (verdicts.length > 1 ? verdicts[verdicts.length - 1] : null))?.focus();
        break;
      }
      default:
    }
  };
  document.addEventListener('keydown', onKey);
  return () => document.removeEventListener('keydown', onKey);
}

// The keys of the review page. `page` has `previous()`, `next()`, `back()`, `toggleSplit()`, `toggleMode()`, `toggleViewed()`,
// `focusVerdict(which)` ('approve' or 'reject') and `focusComment()`, each of which says whether it did anything (a key that does
// nothing here is left to the browser).
export function keepReviewKeys(page) {
  const onKey = (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || !REVIEW_KEYS.has(event.key)) return;
    if (event.shiftKey && event.key !== '?') return;
    if (busy(event)) return;
    const did = (() => {
      switch (event.key) {
        case '?':
          document.getElementById('review-keys-button')?.focus();
          return true;
        case 'j':
        case ']':
          return page.next();
        case 'k':
        case '[':
          return page.previous();
        case 'u':
          return page.back();
        case 'Escape':
          // Escape closes whatever is open first (a hover card or a box); with nothing open it goes back to the list.
          return document.querySelector('.hovercard:not([hidden]), .decision-comment-box') ? false : page.back();
        case 's':
          return page.toggleSplit();
        case 'm':
          return page.toggleMode();
        case 'v':
          return page.toggleViewed();
        case 'a':
          return page.focusVerdict('approve');
        case 'r':
          return page.focusVerdict('reject');
        case 'c':
          return page.focusComment();
        default:
          return false;
      }
    })();
    if (did) event.preventDefault();
  };
  document.addEventListener('keydown', onKey);
  return () => document.removeEventListener('keydown', onKey);
}
