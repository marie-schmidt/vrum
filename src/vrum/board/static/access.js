// Agent access: the keys that let an agent reach this project, and nothing else. A key belongs to
// the one project whose page this is. It is shown once, when it is made, and only a hash of it is
// kept, so a lost key is revoked and replaced. Revoking takes effect on the agent's next request.

import { Refusal, createKey, keys, revokeKey } from './api.js';
import { hrefTo, notify } from './ctx.js';
import { h, moment, plural, put } from './dom.js';
import { dialogForm, field, openDialog, refuse } from './forms.js';
import { icon } from './icons.js';
import { pageHeader } from './page.js';

let root = null;
let parts = {};
let found = [];
let fresh = null; // the key just made, which is shown until the person leaves the page
let generation = 0;

export async function mount(element) {
  root = element;
  fresh = null;
  const mine = ++generation;
  const label = h('input', { type: 'text', id: 'key-label', name: 'label', autocomplete: 'off' });
  const problem = h('p', { class: 'problem', role: 'alert', id: 'key-problem' });
  const submit = h('button', { type: 'submit', class: 'primary', id: 'create-key' }, icon('key', { size: 16 }), 'Create key');
  const address = `${location.origin}/mcp`;
  parts = {
    fresh: h('section', { id: 'new-key', class: 'settings-section key-reveal', 'aria-labelledby': 'new-key-title', hidden: true }),
    list: h('section', { id: 'key-list', class: 'settings-section', 'aria-labelledby': 'keys-title' }, h('p', { class: 'loading', role: 'status' }, 'Loading the keys…')),
  };
  put(
    root,
    h('div', { class: 'settings-page' }, [
      pageHeader('Agent access', 'The keys that let an agent reach this project, and nothing else.'),
      h(
        'section',
        { class: 'settings-section', 'aria-labelledby': 'connect-title' },
        h('h2', { id: 'connect-title' }, 'Connecting an agent'),
        h('p', { class: 'help' }, 'An agent reaches this project with a key. A key belongs to this one project and cannot read or change any other. Make one for each agent, so that you can revoke just that one. A key is shown only once, when it is made.'),
        h(
          'dl',
          { class: 'connect' },
          h('dt', {}, 'The endpoint'),
          h('dd', {}, h('code', { id: 'mcp-address' }, address)),
          h('dt', {}, 'How to send the key'),
          h('dd', {}, 'With every request, in a header: ', h('code', {}, 'Authorization: Bearer <the key>'), '. The ', h('a', { href: hrefTo('tools') }, 'Tools'), ' page lists what an agent can do with it.'),
        ),
      ),
      h(
        'section',
        { class: 'settings-section', 'aria-labelledby': 'make-key-title' },
        h('h2', { id: 'make-key-title' }, 'Make a key'),
        h(
          'form',
          {
            id: 'key-form',
            novalidate: true,
            onsubmit: async (event) => {
              event.preventDefault();
              problem.replaceChildren();
              submit.disabled = true;
              try {
                const made = await createKey(label.value);
                fresh = made;
                label.value = '';
                await load();
                drawFresh(address);
              } catch (error) {
                if (!(error instanceof Refusal)) throw error;
                refuse(problem, error.message);
              } finally {
                submit.disabled = false;
              }
            },
          },
          field('Label for the new key', label, 'Such as the name of the agent that will use it.'),
          problem,
          submit,
        ),
      ),
      parts.fresh,
      parts.list,
    ]),
  );
  try {
    await load();
  } catch (error) {
    if (mine === generation && root && error instanceof Refusal) {
      const failed = h('p', { class: 'problem', role: 'alert' });
      refuse(failed, error.message);
      put(parts.list, failed);
    } else throw error;
  }
}

export function unmount() {
  generation += 1;
  root = null;
  fresh = null;
  parts = {};
  found = [];
}

export function update() {}

// An agent's work is news, and a key that was just used shows when: look at the keys again.
export function refresh() {
  if (root) load().catch(() => {});
}

function drawFresh(address) {
  if (!root || !fresh) return;
  const command = `export VRUM_AGENT_KEY=${fresh.secret}\nclaude mcp add --transport http vrum ${address} --header "Authorization: Bearer $VRUM_AGENT_KEY"`;
  parts.fresh.hidden = false;
  put(
    parts.fresh,
    h('h2', { id: 'new-key-title' }, icon('key', { size: 20 }), `Your new key for “${fresh.key.label}”`),
    h('p', { class: 'help' }, icon('alert', { size: 16 }), 'Copy it now. It is not shown again: if it is lost, revoke it and make another.'),
    h('p', {}, h('code', { id: 'key-secret', class: 'secret' }, fresh.secret)),
    h('details', { open: true, id: 'key-example' }, h('summary', {}, 'Connecting an agent with it'), h('pre', { tabindex: '0' }, h('code', {}, command))),
  );
}

async function load() {
  const mine = generation;
  const list = await keys();
  if (mine !== generation || !root) return;
  found = list;
  drawList();
}

function revokeDialog(key) {
  const { form } = dialogForm({
    fields: [h('p', {}, `Revoke “${key.label}” (${key.hint}…)? Any agent using it is refused on its next request, including one that is connected now. This cannot be undone: make a new key for an agent that should keep working.`)],
    submit: 'Revoke it',
    work: async () => {
      await revokeKey(key.id);
      form.closest('dialog')?.close();
      notify(`Revoked “${key.label}”.`);
      await load();
    },
  });
  openDialog('Revoke a key', [form]);
}

function keyRow(key) {
  const live = key.revoked_at === null;
  return h(
    'li',
    { class: `key${live ? '' : ' revoked'}`, dataset: { key: key.id, state: live ? 'active' : 'revoked' } },
    h(
      'div',
      { class: 'key-main' },
      h('div', { class: 'key-name' }, h('strong', { class: 'label' }, key.label), h('code', { class: 'hint-code' }, `${key.hint}…`)),
      h('span', { class: 'quiet' }, `Made ${moment(key.created_at)}; ${key.last_used_at ? `last used ${moment(key.last_used_at)}` : 'never used'}`),
    ),
    h(
      'div',
      { class: 'key-side' },
      live ? h('span', { class: 'chip ok status-active' }, icon('check', { size: 12 }), 'Active') : h('span', { class: 'chip warn status-revoked' }, icon('lock', { size: 12 }), `Revoked ${moment(key.revoked_at)}`),
      live ? h('button', { type: 'button', class: 'revoke small danger', onclick: () => revokeDialog(key) }, 'Revoke…') : null,
    ),
  );
}

function drawList() {
  const active = found.filter((key) => key.revoked_at === null).length;
  put(
    parts.list,
    h('h2', { id: 'keys-title' }, `Keys (${found.length})`),
    found.length ? h('ul', { id: 'keys', class: 'plain keys' }, found.map(keyRow)) : h('p', { id: 'no-keys', class: 'empty' }, 'There are no keys yet, so no agent can reach this project. Make one above.'),
    found.length ? h('p', { class: 'quiet' }, `${plural(active, 'key')} active.`) : null,
  );
}
