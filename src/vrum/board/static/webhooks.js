// Webhooks (M48), the section of the Users page that only the project's owner has: a webhook sends a small signed message to an
// address when something happens that needs someone (a question waiting, a plan to approve, work blocked or finished, a claim that
// ran out). It is only a nudge: the change feed stays the source of truth, and a message names the kind of news and the item's
// number, never what anything is called or what anyone wrote.
//
// The server decides everything: which kinds there are and what each means (their sentences are drawn as the server words them),
// which addresses may be used (it says why when one may not, and the dialog shows its words), when a webhook is failing and what went
// wrong. This page lists what `webhooks` says and has the owner's controls: add, test, change the kinds, turn off and on, replace the
// secret, read the messages, delete. The signing secret is shown once, in a dialog, when it is made or replaced: it is held by
// nothing on this page (no state, no draft, no address), and the dialog takes it away when it closes. Anyone who is not the
// owner has no such section, and nothing is requested for them.

import { Refusal, Stale, addWebhook, changeWebhook, deleteWebhook, replaceWebhookSecret, testWebhook, webhookDeliveries, webhooks } from './api.js';
import { copyButton } from './copy.js';
import { notify } from './ctx.js';
import { day, h, moment, plural, put } from './dom.js';
import { dialogForm, field, openDialog, refuse } from './forms.js';
import { hoverCard } from './hovercard.js';
import { icon } from './icons.js';
import { rememberFocus } from './records.js';

let section = null; // the place on the page, or null when the page is not shown
let listing = null; // what the server last said: { webhooks, kinds, most }
let generation = 0;
let lastRead = 0; // when the list was last read, so that a burst of news is not a burst of reads
const messagesOpen = new Set(); // the webhooks whose Messages are open, kept while the list is drawn again
const tested = new Map(); // webhook number -> what its last test said, shown for a while
const testing = new Set(); // the webhooks being tested now (a test can take ten seconds)

const READ_EVERY = 5000; // at most one read for news this often
const SHOWN_FOR = 12000; // a test's answer stays this long

// Whether a webhook is sent every kind of news there is.
const everything = (hook) => Boolean(listing) && Object.keys(listing.kinds).every((kind) => hook.events.includes(kind));

// -- the section --------------------------------------------------------------------------------------------

// `place` is the section's element, which the Users page makes only for the owner.
export async function mount(place) {
  section = place;
  listing = null;
  messagesOpen.clear();
  tested.clear();
  testing.clear();
  const mine = ++generation;
  put(section, h('p', { class: 'loading', role: 'status' }, 'Loading the webhooks…'));
  await load(mine);
}

export function unmount() {
  generation += 1;
  section = null;
  listing = null;
  messagesOpen.clear();
  tested.clear();
  testing.clear();
}

// News came in: a webhook may have started failing or been delivered to meanwhile. Looked at again, but not for every piece of news.
export function refresh() {
  if (!section || Date.now() - lastRead < READ_EVERY) return;
  load(generation).catch(() => {});
}

async function load(mine) {
  lastRead = Date.now();
  let read;
  try {
    read = await webhooks();
  } catch (error) {
    if (mine !== generation || !section || error instanceof Stale) return;
    if (!(error instanceof Refusal)) throw error;
    const failed = h('p', { class: 'problem', role: 'alert' });
    refuse(failed, error.message);
    put(section, h('div', { class: 'section-head' }, h('h2', { id: 'users-webhooks-title' }, 'Webhooks')), failed);
    return;
  }
  if (mine !== generation || !section) return;
  listing = read;
  draw();
}

function draw() {
  const restore = rememberFocus(section);
  const { webhooks: found, most } = listing;
  const full = found.length >= most;
  put(
    section,
    h(
      'div',
      { class: 'section-head' },
      h('h2', { id: 'users-webhooks-title' }, 'Webhooks'),
      h('div', { class: 'section-actions', id: 'webhooks-actions' }, full ? null : h('button', { type: 'button', id: 'webhook-open', class: 'primary', onclick: addDialog }, icon('plus', { size: 16 }), 'Add a webhook')),
    ),
    found.length
      ? [h('ul', { id: 'webhooks', class: 'plain webhooks', 'aria-labelledby': 'users-webhooks-title' }, found.map(row)), full ? h('p', { id: 'webhooks-full', class: 'quiet' }, `A project may have ${most} webhooks. Delete one to add another.`) : null]
      : h('p', { id: 'webhooks-empty', class: 'help' }, 'A webhook is a signed message that Vrum sends to an address when something happens that needs someone. It is only a nudge: the change feed stays the source of truth.'),
  );
  restore();
}

// -- one webhook ----------------------------------------------------------------------------------------------

// The one chip a webhook that is not fine carries: turned off, failing (with what went wrong in a hover card) or messages waiting to
// be sent. A webhook that is fine has none.
function stateChip(hook) {
  if (!hook.enabled) return h('span', { class: 'chip webhook-state', dataset: { state: 'off' } }, icon('close', { size: 13 }), 'Turned off');
  if (hook.failing) {
    const chip = h('span', { class: 'chip danger webhook-state', tabindex: 0, dataset: { state: 'failing' } }, icon('alert', { size: 13 }), 'Failing');
    const card = hoverCard(chip, [
      h('p', { class: 'hovercard-title' }, 'The last message did not get through'),
      h('p', {}, hook.last_error || 'The receiver did not take it.'),
      hook.last_failure_at ? h('p', { class: 'quiet' }, `It failed at ${moment(hook.last_failure_at)}.`) : null,
    ]);
    return [chip, card];
  }
  if (hook.pending > 0) return h('span', { class: 'chip info webhook-state', dataset: { state: 'waiting' } }, icon('clock', { size: 13 }), `Waiting to send ${hook.pending}`);
  return null;
}

// The kinds a webhook is sent for as small chips, or one chip for all of them. The server's sentence for each is in its tooltip.
function kindChips(hook) {
  if (everything(hook)) return h('li', { class: 'chip webhook-kind', dataset: { kind: 'all' } }, 'Every kind of news');
  return hook.events.map((kind) => h('li', { class: 'chip webhook-kind', title: listing.kinds[kind] || kind, dataset: { kind } }, kind));
}

// What the last test said, or nothing: drawn in the row for a while, in the server's words.
function testLine(hook) {
  const said = tested.get(hook.id);
  if (!said) return h('p', { class: 'webhook-test', role: 'status' });
  return h('p', { class: `webhook-test ${said.delivered ? 'delivered' : 'not-delivered'}`, role: 'status', dataset: { delivered: String(said.delivered) } }, icon(said.delivered ? 'check' : 'alert', { size: 15 }), h('span', {}, `${said.delivered ? 'Delivered: ' : 'Not delivered: '}${said.words}`));
}

async function test(hook, button) {
  testing.add(hook.id);
  button.disabled = true;
  put(button, 'Testing…');
  let said;
  try {
    said = await testWebhook(hook.id);
  } catch (error) {
    if (error instanceof Stale) return;
    if (!(error instanceof Refusal)) throw error;
    said = { delivered: false, words: error.message };
  } finally {
    testing.delete(hook.id);
  }
  if (!section) return;
  tested.set(hook.id, said);
  setTimeout(() => {
    if (tested.get(hook.id) === said) {
      tested.delete(hook.id);
      if (section) draw();
    }
  }, SHOWN_FOR);
  // A test is an attempt like any other, so what the list says about the webhook may have changed.
  await load(generation);
}

// How a message stands, in words.
function stateWords(one) {
  if (one.state === 'delivered') return 'Delivered';
  if (one.state === 'failed') return `Given up on after ${plural(one.attempts, 'try', 'tries')}`;
  return one.next_attempt_at ? `Waiting for the next try at ${moment(one.next_attempt_at)}` : 'Waiting to be sent';
}

// The messages sent to a webhook, newest first: the kind, how it stands, the tries, the receiver's answer and what went wrong, and when.
function messagesTable(rows) {
  if (!rows.length) return h('p', { class: 'quiet webhook-no-messages' }, 'Nothing has been sent to it yet.');
  return h(
    'div',
    { class: 'table-scroll', role: 'region', tabindex: 0, 'aria-label': 'Messages sent to this webhook' },
    h(
      'table',
      { class: 'webhook-messages-table' },
      h('thead', {}, h('tr', {}, ['Kind', 'State', 'Tries', 'The receiver’s answer', 'What went wrong', 'When'].map((name) => h('th', { scope: 'col' }, name)))),
      h(
        'tbody',
        {},
        rows.map((one) =>
          h(
            'tr',
            { dataset: { message: one.message_id, state: one.state } },
            h('td', {}, one.type),
            h('td', {}, stateWords(one)),
            h('td', { class: 'num' }, String(one.attempts)),
            h('td', { class: 'num' }, one.last_status === null || one.last_status === undefined ? '' : String(one.last_status)),
            h('td', {}, one.last_error || ''),
            h('td', {}, moment(one.created_at)),
          ),
        ),
      ),
    ),
  );
}

// "Messages": a disclosure that reads what was sent when it is opened (and again each time the list is drawn while it is open).
function messagesBlock(hook) {
  const body = h('div', { class: 'webhook-messages-body' });
  const fill = async () => {
    put(body, h('p', { class: 'loading', role: 'status' }, 'Reading the messages…'));
    try {
      const rows = await webhookDeliveries(hook.id, 20);
      if (body.isConnected) put(body, messagesTable(rows));
    } catch (error) {
      if (error instanceof Stale) return;
      if (!(error instanceof Refusal)) throw error;
      const failed = h('p', { class: 'problem', role: 'alert' });
      refuse(failed, error.message);
      if (body.isConnected) put(body, failed);
    }
  };
  const open = messagesOpen.has(hook.id);
  const details = h('details', { class: 'webhook-messages', open, ontoggle: () => {
    if (details.open) {
      messagesOpen.add(hook.id);
      fill();
    } else messagesOpen.delete(hook.id);
  } }, h('summary', { dataset: { focusKey: `messages:${hook.id}` } }, 'Messages'), body);
  return details;
}

function row(hook) {
  const testButton = h('button', { type: 'button', class: 'small webhook-test-button', 'aria-label': 'Send a test message to this webhook', dataset: { focusKey: `test:${hook.id}` }, disabled: testing.has(hook.id), onclick: (event) => test(hook, event.currentTarget) }, testing.has(hook.id) ? 'Testing…' : 'Test');
  const chip = stateChip(hook);
  return h(
    'li',
    { class: `webhook${hook.enabled ? '' : ' off'}`, dataset: { webhook: hook.id, enabled: String(hook.enabled), failing: String(hook.failing) } },
    h(
      'div',
      { class: 'webhook-main' },
      h('p', { class: 'webhook-url' }, h('code', { class: 'webhook-address' }, hook.url)),
      h('ul', { class: 'plain webhook-kinds', 'aria-label': 'What it is sent for' }, kindChips(hook)),
      chip ? h('p', { class: 'webhook-chips' }, chip) : null,
      hook.replacing_secret_until ? h('p', { class: 'quiet webhook-old-secret' }, `The old secret still works until ${day(hook.replacing_secret_until)}.`) : null,
      testLine(hook),
    ),
    h(
      'div',
      { class: 'webhook-side' },
      testButton,
      h('button', { type: 'button', class: 'small webhook-kinds-open', 'aria-label': 'Change the kinds of news sent to this webhook', dataset: { focusKey: `kinds:${hook.id}` }, onclick: () => kindsDialog(hook) }, 'Change kinds…'),
      h('button', { type: 'button', class: 'small webhook-switch', 'aria-label': `${hook.enabled ? 'Turn off' : 'Turn on'} this webhook`, dataset: { focusKey: `switch:${hook.id}` }, onclick: (event) => switchWebhook(hook, event.currentTarget) }, hook.enabled ? 'Turn off' : 'Turn on'),
      h('button', { type: 'button', class: 'small webhook-secret-open', 'aria-label': 'Replace the secret of this webhook', dataset: { focusKey: `secret:${hook.id}` }, onclick: () => replaceDialog(hook) }, 'Replace secret…'),
      h('button', { type: 'button', class: 'small danger webhook-delete', 'aria-label': 'Delete this webhook', dataset: { focusKey: `delete:${hook.id}` }, onclick: () => deleteDialog(hook) }, 'Delete…'),
    ),
    messagesBlock(hook),
  );
}

async function switchWebhook(hook, button) {
  button.disabled = true;
  try {
    await changeWebhook(hook.id, { enabled: !hook.enabled });
    notify(hook.enabled ? 'Turned the webhook off. Nothing is sent to it until you turn it on.' : 'Turned the webhook on.');
  } catch (error) {
    if (error instanceof Stale) return;
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
  await load(generation);
}

// -- the kinds of news, as checkboxes ----------------------------------------------------------------------------

// A list of the kinds of news with the server's sentence for each, as boxes. `chosen` are ticked; with none, none is.
function kindChoices(prefix, chosen = []) {
  const boxes = Object.entries(listing.kinds).map(([kind, words]) => {
    const box = h('input', { type: 'checkbox', id: `${prefix}-${kind}`, name: 'kind', value: kind, checked: chosen.includes(kind) });
    return { kind, box, element: h('label', { class: 'check webhook-kind-choice', for: box.id }, box, h('span', { class: 'webhook-kind-text' }, h('span', {}, words), h('code', { class: 'quiet' }, kind))) };
  });
  return {
    element: h('fieldset', { class: 'webhook-kind-choices' }, h('legend', {}, 'What it is sent for'), boxes.map((one) => one.element)),
    value: () => boxes.filter((one) => one.box.checked).map((one) => one.box.value),
  };
}

// -- the dialogs ----------------------------------------------------------------------------------------------------

// Adding a webhook: the address and the kinds, nothing chosen. The server says what is wrong with an address or an empty choice, in its
// words, here. The secret that comes back is shown once.
function addDialog() {
  const url = h('input', { type: 'text', id: 'webhook-address', name: 'url', autocomplete: 'off', spellcheck: false });
  const kinds = kindChoices('webhook-add-kind');
  const { form } = dialogForm({
    fields: [
      field('The address that receives the messages', url, 'It has to be a secure address on the public internet. The server says why when one cannot be used.'),
      kinds.element,
    ],
    submit: 'Add the webhook',
    work: async () => {
      const made = await addWebhook(url.value, kinds.value());
      form.closest('dialog')?.close();
      notify('Added the webhook.');
      await load(generation);
      secretDialog(made.secret, 'The webhook was added.');
    },
  });
  const box = openDialog('Add a webhook', [form], { wide: true });
  box.id = 'add-webhook-dialog';
  url.focus();
}

// Changing the kinds: opens with the current ones ticked.
function kindsDialog(hook) {
  const kinds = kindChoices('webhook-change-kind', hook.events);
  const { form } = dialogForm({
    fields: [h('p', { class: 'webhook-dialog-address' }, 'Which news is sent to ', h('code', {}, hook.url), '.'), kinds.element],
    submit: 'Save',
    work: async () => {
      await changeWebhook(hook.id, { events: kinds.value() });
      form.closest('dialog')?.close();
      notify('Saved the kinds of news this webhook is sent.');
      await load(generation);
    },
  });
  const box = openDialog('Change the kinds of news', [form], { wide: true });
  box.id = 'change-webhook-dialog';
}

// Replacing the secret: what it does in one sentence, then the new secret, shown once.
function replaceDialog(hook) {
  const { form } = dialogForm({
    fields: [h('p', {}, 'Vrum makes a new secret and shows it to you once. The old secret keeps signing messages too for a day, so the receiver can change over without missing one.')],
    submit: 'Replace the secret',
    work: async () => {
      const made = await replaceWebhookSecret(hook.id);
      form.closest('dialog')?.close();
      notify('Made a new secret for the webhook.');
      await load(generation);
      secretDialog(made.secret, 'The secret was replaced.');
    },
  });
  const box = openDialog('Replace the secret', [form]);
  box.id = 'replace-secret-dialog';
}

function deleteDialog(hook) {
  const { form } = dialogForm({
    fields: [h('p', {}, h('span', {}, 'Delete the webhook for '), h('code', {}, hook.url), '? Nothing more is sent to it, and the messages still waiting are forgotten. This cannot be undone.')],
    submit: 'Delete the webhook',
    work: async () => {
      await deleteWebhook(hook.id);
      form.closest('dialog')?.close();
      notify('Deleted the webhook.');
      messagesOpen.delete(hook.id);
      tested.delete(hook.id);
      await load(generation);
    },
  });
  const box = openDialog('Delete a webhook', [form]);
  box.id = 'delete-webhook-dialog';
}

// The signing secret, shown this once in a dialog that holds it and nothing else holds: it is a variable of this function and the text
// of one element, and the dialog takes it away when it closes.
function secretDialog(secret, lead) {
  const shown = h('code', { id: 'webhook-secret', class: 'secret', tabindex: 0, 'aria-label': 'The secret' }, secret);
  const body = h(
    'div',
    { class: 'webhook-secret-body' },
    h('p', { class: 'webhook-secret-lead' }, lead),
    h('p', { class: 'help webhook-secret-warning' }, icon('alert', { size: 16 }), 'This is the only time the secret is shown. Put it in the place that receives the messages.'),
    h('div', { class: 'secret-line' }, shown, copyButton(shown, 'Copy the secret')),
    h('p', { class: 'quiet' }, 'Every message is signed with it as the Standard Webhooks specification has it, so any library for that can check a message.'),
    h('div', { class: 'actions' }, h('button', { type: 'button', class: 'primary', id: 'webhook-secret-close', onclick: (event) => event.currentTarget.closest('dialog')?.close() }, 'I have put it somewhere safe')),
  );
  const box = openDialog('The secret of the webhook', [body], { wide: true });
  box.id = 'webhook-secret-dialog';
  shown.focus();
}
