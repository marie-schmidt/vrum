// The plans that wait for their agent (M51, drawn as a group in M52): a person asked for changes and the agent has not sent a revision yet.
// Such a plan is not a row of the list (it is the agent's turn, not a person's), so without this group it would vanish. It is a closed group
// after the routine moves, in every way of grouping, with a row for each plan: its kind, its code and title as a link to its review page, who
// asked for changes and how long ago. It is not counted anywhere: not in the sidebar's number, not in "Reviewed N of M", not in the groups'
// counts that add up to the list.

import { Refusal, call, callAll } from './api.js';
import { waitedWords } from './asks.js';
import { viewLink } from './ctx.js';
import { h, plural } from './dom.js';
import { icon } from './icons.js';
import { actorLabel, planCode } from './labels.js';

const MOST_NAMED = 10; // the plans that are named; there are rarely more than a few

// The plans in this state, each with when the changes were asked for and by whom: [{ id, title, revision, asked, by, by_kind }]. Oldest first.
export async function readWaiting() {
  const found = await callAll('list_plans', { status: 'changes_requested' });
  return Promise.all(
    found.slice(0, MOST_NAMED).map(async (plan) => {
      // The request is the plan's last feedback; a plan read whole says when it was given and by whom.
      const whole = (await call('list_plans', { plan: plan.id })).items[0];
      const last = whole.feedback && whole.feedback.length ? whole.feedback[whole.feedback.length - 1] : null;
      return { id: plan.id, title: plan.title, revision: plan.revision, asked: last ? last.at : plan.revised_at, by: last ? last.by : null, by_kind: last ? last.by_kind : null };
    }),
  );
}

// The same, and nothing when the server will not say (the board says on its own when it cannot reach the server).
export const readWaitingQuietly = () =>
  readWaiting().catch((error) => {
    if (error instanceof Refusal) return null;
    throw error;
  });

// One plan of the group.
function row(plan, asOf, by) {
  return h(
    'li',
    { class: 'waiting-row', dataset: { plan: plan.id } },
    icon('plan', { size: 16, className: 'change-lead-icon' }),
    viewLink('review', { change: `plan:${plan.id}`, group: by && by !== 'level' ? by : null }, { class: 'change-title plan-link', dataset: { plan: plan.id } }, h('span', { class: 'change-title-text', dataset: { words: 'theirs' } }, plan.title)),
    h('span', { class: 'change-kind' }, h('span', { class: 'item-code needs-code' }, planCode(plan.id))),
    h(
      'ul',
      { class: 'plain change-facts' },
      plan.by ? h('li', { class: 'change-fact', dataset: { fact: 'by' } }, actorLabel(plan.by, plan.by_kind)) : null,
      h('li', { class: 'change-fact', dataset: { fact: 'waiting' } }, 'Waiting ', h('time', { class: 'needs-time', datetime: plan.asked, title: plan.asked }, waitedWords(plan.asked, asOf))),
      plan.revision > 1 ? h('li', { class: 'change-fact change-revision', dataset: { fact: 'revision' } }, `Revision ${plan.revision}`) : null,
    ),
  );
}

// The closed group, or null when no plan waits. `asOf` is the server's own time; `open` and `toggled(open)` keep whether it is open while
// the page is drawn again; `by` is the way of grouping, which the review page link keeps.
export function agentGroup(plans, asOf, { open, toggled, by }) {
  if (!plans || !plans.length) return null;
  const box = h(
    'details',
    { class: 'agent-group', open, ontoggle: () => toggled(box.open) },
    h(
      'summary',
      { class: 'routine-head agent-head', dataset: { count: String(plans.length), focusKey: 'agent-head' }, onclick: () => toggled(!box.open) },
      icon('chevron_right', { size: 16, className: 'routine-chevron' }),
      icon('clock', { size: 16 }),
      h('h2', { class: 'routine-title group-title' }, 'Waiting for their agent'),
      h('span', { class: 'group-count' }, String(plans.length)),
    ),
    h('ul', { class: 'plain needs-rows agent-rows', 'aria-label': plural(plans.length, 'plan waiting for its agent', 'plans waiting for their agents') }, plans.map((plan) => row(plan, asOf, by))),
  );
  return { box, section: h('section', { class: 'needs-group', dataset: { group: 'agent', by } }, box) };
}
