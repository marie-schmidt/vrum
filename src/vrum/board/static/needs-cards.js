// One change of the page of what needs a person, drawn as a compact card (M52): what it is (its kind with its icon, plain word and code, and the
// level of the work it is about), its title, a Viewed box and a way into the review page; then its facts as chips, a short
// preview, and the one bar every kind of change ends with (decision-bar.js). A card has no sentence in it and no toggle: the whole change,
// its context and its conversation are on the review page (review.js), which the title and Open lead to.
//
// Whether a card is viewed or folded is the page's memory (needs.js keeps it in `view`, never in the browser's storage): this file
// draws it and, when the person changes it, tells the page. A card that is folded keeps its header, its facts and its bar: only the
// preview goes. Everything a card holds is the server's and is drawn as text.

import { effectChip, waitedWords } from './asks.js';
import { can, itemLink, ui, viewLink } from './ctx.js';
import { h, plural, put } from './dom.js';
import { icon } from './icons.js';
import { actorLabel, codeOf, itemSizeChip, kindIcon } from './labels.js';
import { LEVEL_WORDS } from './changes.js';
import { decisionBar } from './decision-bar.js';
import { warnWords, worstOf } from './needs-assist.js';
import { previewOf } from './needs-preview.js';
import { store } from './store.js';

// -- title and facts shared by cards and the review page -----------------------------------------------------

const refCode = (ref) => itemLink(ref, { class: 'item-link', dataset: { ref } }, codeOf(ref));

export function titleNodes(change) {
  const theirs = (words) => h('span', { class: 'change-title-text', dataset: { words: 'theirs' } }, words);
  if (change.look !== 'placement') return [theirs(change.title)];
  const move = change.item.placement;
  const refs = move.items.map((one) => one.ref);
  const named = refs.length > 3 ? refs.slice(0, 2) : refs;
  const parts = ['Move '];
  named.forEach((ref, at) => {
    if (at) parts.push(refs.length <= 3 && at === named.length - 1 ? ' and ' : ', ');
    parts.push(refCode(ref));
  });
  if (refs.length > 3) parts.push(` and ${refs.length - 2} more`);
  parts.push(' under ');
  if (move.parent) parts.push(refCode(move.parent), ' ', theirs(move.parent_title ?? store.nodes.get(move.parent)?.title ?? ''));
  else parts.push('a new epic, ', theirs(move.new_epic?.title ?? ''));
  return parts;
}

// What the decision covers, in plain words: "7 items", "4 items", "1 check open", "2 notes", "lapsed 2 times". Null when already said
// elsewhere (a plan with line approvals already shows its total in "4 of 7 approved"; a move of up to 3 items names them in its title).
export function impactWords(change) {
  const { item } = change;
  if (change.look === 'plan') {
    if (item.plan.state !== 'proposed') return null;
    if (change.review && change.review.count > 0) return null;
    return plural(item.plan.epics + item.plan.stories + item.plan.tasks, 'item');
  }
  if (change.look === 'placement') return item.placement.items.length > 3 ? plural(item.placement.items.length, 'item') : null;
  if (change.look === 'check') return `${plural(item.count, 'check')} open`;
  if (change.look === 'note') return plural(item.count, 'note');
  if (change.look === 'lapse') return `lapsed ${plural(item.count, 'time')}`;
  return null;
}

const fact = (name, ...content) => h('li', { class: 'change-fact', dataset: { fact: name } }, ...content);

// The byline row. `asOf` is the server's own time, so how long something has waited never depends on this browser's clock.
export function factsOf(change, asOf) {
  const { item } = change;
  const out = [];
  if (item.waiting) out.push(fact('by', actorLabel(item.waiting, item.waiting_kind)));
  const decided = change.look === 'plan' && (item.plan.state === 'approved' || item.plan.state === 'rejected');
  if (!decided) out.push(fact('waiting', 'Waiting ', h('time', { class: 'needs-time', datetime: item.waiting_since, title: item.waiting_since }, waitedWords(item.waiting_since, asOf))));
  if (change.look === 'plan' && item.plan.revision > 1) out.push(h('li', { class: 'change-fact change-revision', dataset: { fact: 'revision', revision: item.plan.revision } }, `Revision ${item.plan.revision}`));
  // How much of the plan is approved (carried from earlier or waiting to be sent); left out when nothing is.
  if (change.look === 'plan' && change.review && change.review.count > 0) out.push(h('li', { class: 'change-fact change-review-count', dataset: { fact: 'review', approved: change.review.count, total: change.review.total } }, icon('check', { size: 13 }), `${change.review.count} of ${change.review.total} approved`));
  const impact = impactWords(change);
  if (impact) out.push(fact('impact', impact));
  if (item.ask) out.push(h('li', { class: 'change-fact change-fact-holder', dataset: { fact: 'effect' } }, effectChip(item.ask)));
  if (change.warn && change.warn.total) out.push(h('li', { class: 'chip change-fact change-warning', dataset: { fact: 'warnings', state: worstOf(change.warn) } }, icon('alert', { size: 13 }), warnWords(change.warn)));
  const node = change.look === 'plan' || change.look === 'placement' ? null : store.nodes.get(item.node);
  const size = node ? itemSizeChip(node, { labelled: true }) : null;
  if (size) out.push(h('li', { class: 'change-fact change-fact-holder', dataset: { fact: 'size' } }, size));
  return h('ul', { class: 'plain change-facts' }, out);
}

// The level of the work, as a chip with its icon and word. A plan and a note say what they are in their kind word already.
export const levelChip = (level) =>
  level && level !== 'plan' && level !== 'note' ? h('span', { class: 'chip level-chip', dataset: { level } }, kindIcon(level, 13), LEVEL_WORDS[level] ?? level) : null;

// The address of a change's review page, keeping the way of grouping the person was shown so that the page's previous and next follow
// the same order.
export const reviewChoices = (change, by) => ({ change: change.id, group: by && by !== 'level' ? by : null });

// The kind of a change with its icon, plain word (only when the code or title does not already say it) and muted code
// ("Plan 1415", "Proposal 1367", "Q-522", "T-160", "Gate 1382").
export const kindChip = (change, { withIcon = true } = {}) => {
  const skipWord = change.code.toLowerCase().startsWith(change.words.toLowerCase()) || change.look === 'placement';
  return h(
    'span',
    { class: 'change-kind' },
    withIcon ? icon(change.icon, { size: 16 }) : null,
    skipWord ? null : h('span', { class: 'change-kind-word' }, change.words),
    h('span', { class: `item-code needs-code${change.item.ask ? ' ask-code' : ''}` }, change.code),
  );
};

// The card. `view` is the page's memory of it (viewed, folded, whether the preview is opened out), `fp` is what the server sent for it,
// `entry` is what was read for it (needs-details.js) and `hooks` are what a decision tells the page. `onChrome()` is called when the
// card's viewed mark changes, so that the page's counts and rail follow. Returns the element and what the page needs to work it.
export function cardOf(change, { view, fp, entry, asOf, hooks, onChrome, by }) {
  const { item } = change;
  const isPlan = change.look === 'plan';
  const loading = isPlan ? !(entry && entry.data) && !(entry && entry.state === 'failed') : entry !== null && entry.state === 'loading' && !entry.data;
  const reading = change.look === 'placement' && change.assist.reading;
  const choices = reviewChoices(change, by);

  // The title leads the visual hierarchy and opens the review page. A suggested move's title holds links to the items it moves, and a link
  // cannot hold a link, so there it is a plain element that opens the page when its words are pressed (Review is the link for the keyboard).
  const title =
    change.look === 'placement'
      ? h('span', {
          class: 'change-title',
          dataset: { focusKey: `title:${change.key}` },
          onclick: (event) => {
            if (event.target.closest('a') || event.button) return;
            ui.navigate(choices, { page: 'review' });
          },
        }, titleNodes(change))
      : viewLink('review', choices, { class: 'change-title', dataset: { focusKey: `title:${change.key}` } }, titleNodes(change));
  const open = viewLink('review', choices, { class: 'action-link small change-open', 'aria-label': `Review ${change.code} ${change.title}`, dataset: { focusKey: `open:${change.key}` } }, 'Review', icon('arrow_right', { size: 13 }));
  const viewed = h('input', { type: 'checkbox', class: 'change-viewed', 'aria-label': `Viewed ${change.name}`, dataset: { focusKey: `viewed:${change.key}` } });
  viewed.disabled = loading;
  viewed.addEventListener('change', () => card.setViewed(viewed.checked));
  const changed = h('span', { class: 'chip change-changed', role: 'status', hidden: true }, 'Changed since viewed');
  const head = h(
    'header',
    { class: 'change-head', tabindex: -1, dataset: { focusKey: `head:${change.key}` } },
    h(
      'div',
      { class: 'change-lead' },
      icon(change.icon, { size: 16, className: 'change-lead-icon' }),
      title,
      kindChip(change, { withIcon: false }),
      // Omit the level chip when already grouped by level so cards inside "Stories" or "Tasks" do not stutter.
      by === 'level' ? null : levelChip(change.level),
      changed,
    ),
    h('span', { class: 'change-tools' }, h('label', { class: 'change-viewed-label' }, viewed, h('span', {}, 'Viewed')), open),
  );

  const preview = h('div', { class: 'change-preview', id: `preview-${change.key.replace(/[^a-z0-9]+/gi, '-')}` });
  const drawPreview = () =>
    put(
      preview,
      previewOf(change, {
        entry,
        hooks,
        expanded: view.open.has('preview'),
        onMore: () => {
          view.open.add('preview');
          drawPreview();
        },
      }),
    );
  drawPreview();

  // The bar of the whole change: none while a move is still being read, none for a viewer, and none for a row of several notes or checks,
  // whose bars are under their lines in the preview.
  const bar = reading ? null : decisionBar(change, hooks, entry);

  const li = h(
    'li',
    {
      class: `needs-row needs-${item.kind}`,
      dataset: {
        kind: item.kind,
        node: item.node,
        ask: item.ask ? item.ask.id : null,
        plan: item.plan ? item.plan.id : null,
        placement: item.placement ? item.placement.id : null,
        gate: item.gate ? item.gate.id : null,
        count: item.count,
        change: change.id,
        key: change.key,
        level: change.level,
        decision: change.decision,
        routine: change.routine ? 'true' : null,
        warning: change.warn && change.warn.total ? 'true' : null,
        reading: reading ? 'true' : null,
        revision: isPlan ? item.plan.revision : null,
      },
    },
    head,
    factsOf(change, asOf),
    preview,
    bar,
  );

  // Draws the page's memory of the card: viewed, folded, and that it changed since it was looked at.
  const apply = () => {
    const isViewed = view.viewed !== null;
    li.dataset.viewed = String(isViewed);
    li.dataset.collapsed = String(view.collapsed);
    li.dataset.changed = String(view.changed && !isViewed);
    changed.hidden = !(view.changed && !isViewed);
    viewed.checked = isViewed;
    preview.hidden = view.collapsed;
  };

  const card = {
    li,
    change,
    head,
    apply,
    // Marking it viewed folds it, as in a pull request; marking it not viewed opens it again.
    setViewed(on) {
      view.viewed = on ? fp : null;
      view.changed = false;
      view.collapsed = on;
      apply();
      onChrome();
    },
    setCollapsed(on) {
      view.collapsed = on;
      apply();
    },
    // What the keys do (needs-keys.js): the same as pressing the box and the button.
    toggleViewed() {
      if (!viewed.disabled) card.setViewed(view.viewed === null);
    },
    toggleCollapsed() {
      card.setCollapsed(!view.collapsed);
    },
    // The buttons that decide, in the order they are drawn (a key only moves the focus to one).
    verdicts() {
      return [...li.querySelectorAll('button.verdict')].filter((one) => one.checkVisibility() && !one.disabled);
    },
    // Whether the person can decide here at all.
    decides: () => can('write'),
  };
  apply();
  return card;
}
