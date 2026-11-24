// What the card of a person or an agent says (labels.js `actorLabel` opens it, and loads this file the first time): who they
// are in this project, what they hold now and the last handoff note they left. Everything comes from what the server says
// now (who is working, the members, and, for the owner, the agent keys), read once for a few seconds and shared by every card, and
// read again when the board has heard news since. What a holder said about where the work is (its branch, commit and pull request,
// and which run it is) is under each piece of work they hold, and under their last handoff note.

import { Refusal, call, keys as listKeys, members as listMembers } from './api.js';
import { can, isMe, itemLink } from './ctx.js';
import { h, moment, put } from './dom.js';
import { allowance, callingWords } from './key-tools.js';
import { actorLabel, codeOf, expiryChip, roleChip, verifierChip } from './labels.js';
import { store } from './store.js';
import { whereBlock } from './whereabouts.js';

const FRESH = 15000; // how long what was read stays good, in milliseconds
let cached = null; // { at, cursor, ask } where ask is the promise of the three reads and cursor the newest change the board had heard of

async function read(wanted) {
  const quiet = (promise) => promise.catch((error) => (error instanceof Refusal ? null : Promise.reject(error)));
  const [who, people, agents] = await Promise.all([
    quiet(call('who_is_working', {})),
    quiet(listMembers()),
    wanted.agents && can('manage') ? quiet(listKeys()) : Promise.resolve(null),
  ]);
  return { who, people: people || [], agents: agents || [] };
}

function load(kind) {
  const now = Date.now();
  // A read made for a person has no keys in it: an agent's card asks again for those.
  if (!cached || now - cached.at > FRESH || cached.cursor !== store.cursor || (kind === 'agent' && !cached.agents)) cached = { at: now, cursor: store.cursor, agents: kind === 'agent', ask: read({ agents: kind === 'agent' }) };
  return cached.ask;
}

function timeLeft(expires) {
  const ms = Date.parse(expires) - Date.now();
  const seconds = Math.round(Math.abs(ms) / 1000);
  const words = seconds >= 3600 ? `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min` : seconds >= 60 ? `${Math.floor(seconds / 60)} min` : `${seconds} s`;
  return ms > 0 ? `${words} left` : `ran out ${words} ago`;
}

const fact = (label, value) => (value ? [h('dt', {}, label), h('dd', {}, value)] : []);

const heldList = (title, rows, lapsed = false) =>
  rows.length
    ? h(
        'section',
        { class: `actor-card-part${lapsed ? ' lapsed' : ''}` },
        h('h4', {}, title),
        h(
          'ul',
          { class: 'hovercard-list' },
          rows.map((work) =>
            h(
              'li',
              { dataset: { node: work.node } },
              itemLink(work.node, { class: 'item-link' }, h('span', { class: 'id mono' }, codeOf(work.node)), ` ${work.title}`),
              h('span', { class: 'quiet' }, timeLeft(work.expires_at)),
              whereBlock(work),
            ),
          ),
        ),
      )
    : null;

function face(name, kind, data) {
  const mine = data.who?.agents?.find((one) => one.actor === name && one.kind === kind) || null;
  const member = kind === 'person' ? data.people.find((one) => one.name === name) : null;
  const key = kind === 'agent' ? data.agents.find((one) => one.name === name && one.revoked_at === null) : null;
  const handoff = mine?.last_handoff || null;
  const facts = [
    ...(member ? [...fact('Joined', moment(member.joined_at)), ...fact('Email', member.email)] : []),
    ...(key
      ? [
          ...fact('Last used', key.last_used_at ? moment(key.last_used_at) : 'Never'),
          ...fact('Key', expiryChip(key)),
          ...fact('May do', allowance(null, key.tools).label),
          ...fact('Calls Vrum', callingWords(key.code_mode)),
        ]
      : []),
  ];
  const holding = mine?.holding || [];
  const lapsed = mine?.lapsed || [];
  return [
    h(
      'header',
      { class: 'actor-card-head' },
      h('strong', {}, actorLabel(name, kind, { size: 18, card: false })),
      member ? roleChip(member.role) : null,
      key?.verifier ? verifierChip() : null,
      isMe(name, kind) ? h('span', { class: 'chip you' }, 'You') : null,
    ),
    h('p', { class: 'quiet actor-card-kind' }, kind === 'agent' ? 'An agent: it works through its key' : 'A person in this project'),
    facts.length ? h('dl', { class: 'actor-card-facts' }, facts) : null,
    heldList(`Holding now (${holding.length})`, holding),
    heldList('Ran out on them', lapsed, true),
    handoff
      ? h(
          'section',
          { class: 'actor-card-part' },
          h('h4', {}, 'Last handoff'),
          h(
            'p',
            {},
            handoff.outcome ? `${handoff.outcome} ` : '',
            handoff.node ? itemLink(handoff.node, { class: 'item-link' }, h('span', { class: 'id mono' }, codeOf(handoff.node)), ` ${handoff.title || ''}`) : null,
            h('span', { class: 'quiet' }, ` ${moment(handoff.at)}`),
          ),
          whereBlock(handoff),
          handoff.note ? h('blockquote', { class: 'actor-card-note' }, handoff.note.length > 220 ? `${handoff.note.slice(0, 219)}…` : handoff.note) : null,
        )
      : null,
    !holding.length && !lapsed.length && !handoff ? h('p', { class: 'quiet' }, 'Holds nothing and has left no handoff note.') : null,
  ];
}

// Fills the card `body` for one person or agent and places the card again once it has its words.
export async function fillActorCard(body, name, kind, update) {
  put(body, h('p', { class: 'quiet' }, 'Looking them up…'));
  update();
  try {
    put(body, ...face(name, kind, await load(kind)));
  } catch {
    put(body, h('strong', {}, actorLabel(name, kind, { size: 18, card: false })), h('p', { class: 'quiet' }, 'Could not look them up now.'));
  }
  update();
}
