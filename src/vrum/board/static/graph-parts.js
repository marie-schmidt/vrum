// Small pieces the parts of the Graph page share: how an item stands, as chips with a word and an
// icon (never colour alone), a code in the mono face, and a way to set a custom property on an
// element (the page's own policy turns style attributes away, so sizes and places are set from
// script, and a property of the form --name is set with setProperty).

import { day, h } from './dom.js';
import { icon } from './icons.js';
import { plainNoteStatus, plainRequirementStatus, statusChip } from './labels.js';
import { lateDays } from './graph-model.js';

export function vars(element, values) {
  for (const [name, value] of Object.entries(values)) element.style.setProperty(name, String(value));
  return element;
}

export const codeSpan = (text) => h('span', { class: 'graph-code' }, text);

export const chip = (className, ...children) => h('span', { class: `chip ${className}`.trim() }, ...children);

export function dueChip(node) {
  const late = lateDays(node);
  if (late > 0) return chip('overdue', icon('clock', { size: 14 }), `Overdue ${late} ${late === 1 ? 'day' : 'days'}`);
  return node.target_on ? chip('due', icon('calendar', { size: 14 }), `Due ${day(node.target_on)}`) : null;
}

// Where an item stands, as one chip: a work item's status, an acceptance criterion's (Open, Met, Dropped) or a note's
// (Unconfirmed, Confirmed, Replaced, Withdrawn). A replaced note says in its tooltip which note replaced it.
// The Around view styles an unconfirmed chip with an outline and an ended one as faded, by these two classes.
export function standingChips(item) {
  const record = item.record;
  if (item.work) return [statusChip(record.status), dueChip(record)];
  if (item.kind === 'rule') return [plainRequirementStatus(record.status)];
  const shown = plainNoteStatus(record.status);
  if (record.status === 'unconfirmed') shown.classList.add('unconfirmed');
  if (record.status === 'superseded' || record.status === 'withdrawn') shown.classList.add('ended');
  if (record.status === 'superseded' && record.superseded_by_id) shown.title = `Replaced by note #${record.superseded_by_id}`;
  return [shown];
}
