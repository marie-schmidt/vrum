// The definition of done as checks. Its list lines (a dash, then the words) are things to tick on every story
// and task, by a person here or by an agent; the section of a story's or task's page draws them as boxes, with
// who ticked each one, when, and how it was checked (the evidence, in full). Ticking asks nothing and is saved
// at once, by the operation an agent calls; "Add how you checked" opens a one-line field that ticks it with
// those words. A person who can only look sees the boxes, disabled. Every other line of the text is context,
// shown as it was written above the boxes. A line may start with tags that say who may tick it ([person], [verifier],
// [github]); a check carries one chip for them. When an agent ticked a [github] check, the evidence starts with a pull request
// address, which is a link to look again, and a quiet line says that the agent reported it and GitHub did not confirm it:
// Vrum never asks GitHub.
//
// `checkItems` and `itemWords` read a text the way the server does (definition_of_done.py), so that the
// Settings page can say how many checks a text holds before it is saved.

import { Refusal, call } from './api.js';
import { can, notify, viewLink } from './ctx.js';
import { announce, h, moment } from './dom.js';
import { refuse } from './forms.js';
import { actorLabel, checkChip } from './labels.js';
import { renderMarkdown } from './markdown.js';
import { sync } from './store.js';
import { plainHttps } from './whereabouts.js';

const LIST_LINE = /^[ \t]*[-*][ \t]+(\S.*)$/;
const CHECKBOX = /^\[[ xX]\](?:\s+|$)/;
const TAG = /^\[(?:person|verifier|github)\](?:\s+|$)/i;
const RULER = /^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const FENCE = /^[ \t]*(?:```|~~~)/;

// The words of a list line as a check has them: trimmed, a leading [ ] or [x] and then any [person], [verifier] or
// [github] tags (in any order) taken off, white space made one space.
export function itemWords(text) {
  let rest = String(text).trim().replace(CHECKBOX, '');
  while (TAG.test(rest)) rest = rest.replace(TAG, '');
  return rest.split(/\s+/).filter(Boolean).join(' ');
}

// The pull request address a piece of evidence starts with, as a link a person may follow: an https address whose path is
// exactly /OWNER/REPO/pull/NUMBER, with nothing after it but the words of what was seen. Anything else (another scheme, a
// page of the repository, an address with a login, a query or a fragment) is not one, and is left as plain text
// (whereabouts.js decides that, for every page).
export function pullRequestOf(evidence) {
  const written = String(evidence || '');
  const start = written.search(/\S/);
  if (start < 0) return null;
  const address = written.slice(start).split(/\s/, 1)[0];
  const url = plainHttps(address, { pullRequest: true });
  if (!url) return null;
  return { address, href: url.href, after: written.slice(start + address.length) };
}

// The checks a text holds, in the order written (a list line inside a code block, a line across the page and two
// lines with the same words are not checks, or not two), and the rest of the text, which is context.
function scan(text) {
  const items = [];
  const seen = new Set();
  const context = [];
  let fenced = false;
  for (const line of String(text || '').split(/\r?\n/)) {
    if (FENCE.test(line)) {
      fenced = !fenced;
      context.push(line);
      continue;
    }
    const match = fenced || RULER.test(line) ? null : LIST_LINE.exec(line);
    const words = match ? itemWords(match[1]) : '';
    if (!words) {
      context.push(line);
      continue;
    }
    if (!seen.has(words)) {
      seen.add(words);
      items.push(words);
    }
  }
  return { items, context: context.join('\n').trim() };
}

export const checkItems = (text) => scan(text).items;

// What is left of a text when its checks are taken out: the lines that are context, as written.
export const contextOf = (text) => scan(text).context;

// "4 of 6 ticked".
const tickedWords = (ticked, total) => `${ticked} of ${total} ticked`;

// The section of the definition of done on the page of a story or task, with every check as a box. `node` is the
// item as get_node sent it (its `checks`), `definition` the project's definition of done and `state` the card's
// (the drafts of "how you checked" live in it, so that a redraw never loses one).
export function checksSection({ node, definition, state }) {
  const writes = can('write');
  state.evidence = state.evidence || new Map();
  let checks = node.checks;
  let holder = null;

  function swap(result) {
    checks = result.checks;
    const key = document.activeElement?.dataset?.focusKey;
    const fresh = build();
    holder.replaceWith(fresh);
    holder = fresh;
    if (key) [...fresh.querySelectorAll('[data-focus-key]')].find((one) => one.dataset.focusKey === key)?.focus();
  }

  // Tick or untick. A refusal is said in the server's words: beside the field when there is one, else as a notice,
  // and the box goes back to what it was.
  async function save(check, wanted, evidence, { control, problem = null }) {
    control.disabled = true;
    try {
      const result = await call('set_check', { node: node.ref, item: check.item, checked: wanted, evidence: evidence ? evidence.trim() : null });
      state.evidence.delete(check.item);
      state.dirty = false;
      swap(result);
      announce(`${wanted ? 'Ticked' : 'Unticked'}: ${check.text}`);
      await sync();
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      control.disabled = false;
      if (problem) refuse(problem, error.message);
      else {
        control.checked = !wanted;
        notify(error.message, { refusal: true });
      }
    }
  }

  function howForm(check) {
    const input = h('input', {
      type: 'text',
      class: 'check-how-field',
      maxlength: 2000,
      autocomplete: 'off',
      value: state.evidence.get(check.item) || '',
      placeholder: 'How you checked, e.g. 212 tests pass',
      'aria-label': `How you checked: ${check.text}`,
      dataset: { focusKey: `how:${check.item}` },
      oninput: () => state.evidence.set(check.item, input.value),
    });
    const problem = h('div', { class: 'problem', role: 'alert' });
    const stop = () => {
      state.evidence.delete(check.item);
      state.dirty = false;
      swap({ checks, checks_ticked: checks.filter((one) => one.checked).length, checks_total: checks.length });
      holder.querySelector(`[data-focus-key="how-button:${check.item}"]`)?.focus();
    };
    const saveButton = h('button', { type: 'submit', class: 'small primary' }, 'Save');
    return h(
      'form',
      {
        class: 'check-form',
        onsubmit: async (event) => {
          event.preventDefault();
          problem.replaceChildren();
          if (!input.value.trim()) {
            refuse(problem, 'Say how you checked, or cancel.');
            return;
          }
          await save(check, true, input.value, { control: saveButton, problem });
        },
        onkeydown: (event) => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            stop();
          }
        },
      },
      input,
      saveButton,
      h('button', { type: 'button', class: 'small', onclick: stop }, 'Cancel'),
      problem,
    );
  }

  function row(check) {
    const id = `check-${check.item}`;
    const box = h('input', {
      type: 'checkbox',
      id,
      class: 'check-box',
      checked: check.checked,
      disabled: !writes,
      'aria-describedby': check.checked ? `${id}-who` : null,
      dataset: { focusKey: `check:${check.item}` },
      // The page treats a change in it as someone typing in the card, which a tick is not.
      onchange: (event) => {
        event.stopPropagation();
        save(check, box.checked, null, { control: box });
      },
    });
    const openForm = () => {
      state.evidence.set(check.item, state.evidence.get(check.item) ?? '');
      swap({ checks, checks_ticked: checks.filter((one) => one.checked).length, checks_total: checks.length });
      holder.querySelector(`[data-focus-key="how:${check.item}"]`)?.focus();
    };
    const how = writes
      ? h('button', { type: 'button', class: 'link check-how', dataset: { focusKey: `how-button:${check.item}` }, onclick: openForm }, check.evidence ? 'Change how you checked' : 'Add how you checked')
      : null;
    // What an agent reported about a pull request is not something Vrum looked at: the address is a link to look again, and the
    // line says so. A person's tick, and any other check, is shown as written.
    const reported = check.checked && check.github && check.checked_by_kind === 'agent';
    const pull = reported ? pullRequestOf(check.evidence) : null;
    const evidence = check.checked && check.evidence
      ? h(
          'p',
          { class: 'check-evidence' },
          pull ? [h('a', { class: 'external check-pull-request', href: pull.href, target: '_blank', rel: 'noopener noreferrer' }, pull.address), pull.after] : check.evidence,
        )
      : null;
    return h(
      'li',
      { class: `check-item${check.checked ? ' ticked' : ''}`, dataset: { item: check.item, checked: String(check.checked) } },
      h(
        'div',
        { class: 'check-line' },
        h('label', { class: 'check-label', for: id }, box, h('span', { class: 'check-text' }, check.text), checkChip(check)),
        check.checked ? h('span', { class: 'check-who quiet', id: `${id}-who` }, actorLabel(check.checked_by, check.checked_by_kind), `, ${moment(check.checked_at)}`) : null,
        state.evidence.has(check.item) ? null : how,
      ),
      evidence,
      reported ? h('p', { class: 'check-reported quiet' }, 'Reported by ', actorLabel(check.checked_by, check.checked_by_kind), ', not confirmed by GitHub.') : null,
      state.evidence.has(check.item) ? howForm(check) : null,
    );
  }

  function build() {
    const ticked = checks.filter((check) => check.checked).length;
    const context = scan(definition.body).context;
    return h(
      'section',
      { class: 'part check-part', dataset: { part: 'definition' } },
      h(
        'div',
        { class: 'part-head' },
        h('h3', {}, 'Definition of done', h('span', { class: 'heading-count', id: 'checks-ticked' }, tickedWords(ticked, checks.length))),
        writes ? viewLink('definition', {}, { class: 'action-link small edit-definition' }, 'Edit in Settings') : null,
      ),
      context ? h('div', { class: 'body-text check-intro' }, renderMarkdown(context)) : null,
      h('ul', { class: 'plain check-list', 'aria-label': `Checks of the definition of done, ${tickedWords(ticked, checks.length)}` }, checks.map(row)),
    );
  }

  holder = build();
  return {
    get element() {
      return holder;
    },
  };
}
