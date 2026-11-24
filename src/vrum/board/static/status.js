// Status and export: how the server is, as the server says it, and a download of the whole export. The page
// says only what a person needs: that the server is running, the state of search by meaning, whether it can
// send email, and the export. Everything else the server reports (versions, the state of the index and of the
// embedding service, how much each kind of thing there is, the other ways to sign in) is behind one closed
// Technical details. The database password, the mail server's user and password and the sign-in providers'
// secrets are not here, because the server never says them. Import and backup are not done from a page.

import { Refusal, call, get } from './api.js';
import { h, moment, plural, put } from './dom.js';
import { refuse } from './forms.js';
import { icon } from './icons.js';
import { pageHeader } from './page.js';
import { store } from './store.js';

const SIZES = [
  ['epics', 'Epics'],
  ['stories', 'Stories'],
  ['tasks', 'Tasks'],
  ['links', 'Links'],
  ['rules', 'Acceptance criteria'],
  ['notes', 'Notes'],
  ['events', 'Changes in the history'],
];

const SERVICE = {
  not_configured: 'No embedding service is set up',
  answering: 'Answering',
  unreachable: 'Not answering',
};

// Search by meaning in one plain state. When it is in use and items are still waiting to be indexed, it says how many.
function meaningWords(index) {
  switch (index.meaning) {
    case 'used':
      return index.waiting_to_index > 0 ? `In use, ${plural(index.waiting_to_index, 'item')} waiting to be indexed` : 'In use';
    case 'not_configured':
      return 'Not set up';
    case 'service_unreachable':
      return 'Not answering: the last try to index could not reach the embedding service';
    case 'index_not_ready':
      return 'Not ready yet: the index is not built, or was built with another model';
    default:
      return index.meaning;
  }
}

let root = null;
let places = null; // the parts of the page that are drawn again with every answer
let generation = 0;
let timer = null;

export function mount(element) {
  root = element;
  // The technical details are made once and only their content is drawn again, so that a person who opened them is not shut out by the next answer.
  places = {
    summary: h('div', { id: 'status-body', class: 'status-body' }, h('p', { class: 'loading', role: 'status' }, 'Asking the server…')),
    technical: h('div', { class: 'technical-body', id: 'technical-body' }),
  };
  put(
    root,
    h('div', { class: 'settings-page status-page' }, [
      pageHeader('Status and export'),
      h('p', { class: 'problem', role: 'alert', id: 'status-problem' }),
      places.summary,
      exportSection(),
      h('details', { id: 'technical', class: 'status-technical' }, h('summary', {}, 'Technical details'), places.technical),
    ]),
  );
  load();
  timer = setInterval(load, Math.max(10, store.info.poll_seconds * 2) * 1000);
}

export function unmount() {
  generation += 1;
  root = null;
  places = null;
  clearInterval(timer);
  timer = null;
}

export function update() {}

export function refresh() {
  if (root) load();
}

export function index() {
  if (root) load();
}

async function load() {
  const mine = ++generation;
  try {
    const [status, info] = await Promise.all([call('server_status', {}), get('/board/api/info')]);
    if (mine !== generation || !root) return;
    root.querySelector('#status-problem').replaceChildren();
    draw(status, info.mail, info.sign_in);
  } catch (error) {
    if (mine !== generation || !root) return;
    if (!(error instanceof Refusal)) throw error;
    refuse(root.querySelector('#status-problem'), error.message);
  }
}

const row = (name, label, value) => h('tr', { dataset: { name } }, h('th', { scope: 'row' }, label), h('td', { class: 'value' }, value));

// A table of facts that scrolls inside its own box when it is wider than the page.
const facts = (label, ...rows) => h('div', { class: 'table-scroll', role: 'region', tabindex: '0', 'aria-label': label }, h('table', { class: 'status-facts' }, h('tbody', {}, rows)));

// Whether the server can send email, in words.
function emailWords(mail) {
  return mail && mail.enabled ? 'Set up' : 'Not set up: invitations are shared by link and passwords cannot be reset by email';
}

// Whom the server sends email as and through, for the technical details. Never a user name or a password:
// the server does not say them.
const sendingWords = (mail) => `Sending as ${mail.from} through ${mail.host}`;

const KINDS = { oidc: 'OpenID Connect', github: 'GitHub', ldap: 'LDAP directory' };

// The other ways to sign in, when the server has any: each provider and the address it talks to, which
// email domains may use it, and whether a first sign-in may make an account. Never a client secret, a
// password or a user name: the server does not say them.
function signInRows(signIn) {
  if (!signIn || !Array.isArray(signIn.providers) || signIn.providers.length === 0) return null;
  const domains = signIn.allowed_domains || {};
  const who = (id) => (Array.isArray(domains[id]) && domains[id].length ? `Only email addresses at ${domains[id].join(', ')}` : 'Any email domain');
  // What a way is, and under it in quiet words who may use it: two columns, so that a narrow page has room.
  const line = (name, label, value, note) =>
    h('tr', { dataset: { name } }, h('th', { scope: 'row' }, label), h('td', { class: 'value' }, value, note ? h('span', { class: 'value-note' }, note) : null));
  const rows = signIn.providers.map((provider) => line(`provider-${provider.id}`, provider.name, `${KINDS[provider.kind || provider.id] || provider.kind || provider.id}${provider.host ? `, at ${provider.host}` : ''}`, who(provider.id)));
  rows.push(line('create-accounts', 'New accounts', signIn.create_accounts === false ? 'A first sign-in makes no account: only people who already have one can use it' : 'A first sign-in makes a new account', null));
  return rows;
}

// A group of facts in the technical details, under a quiet heading.
const group = (id, title, label, rows) => (rows ? h('section', { id, class: 'technical-group' }, h('h3', {}, title), facts(label, rows)) : null);

function draw(status, mail, signIn) {
  const index = status.index;
  const last = index.last_reindex;
  put(
    places.summary,
    h(
      'section',
      { id: 'status-summary', class: 'settings-section' },
      h('p', { class: 'ok status-line' }, icon('check', { size: 16 }), h('strong', {}, 'The server is running and can reach its database.')),
      facts('Search by meaning and email', row('meaning', 'Search by meaning', meaningWords(index)), row('mail', 'Email', emailWords(mail))),
    ),
  );
  put(
    places.technical,
    group('versions', 'The server', 'The server’s versions', [row('vrum_version', 'Vrum', status.vrum_version), row('postgres_version', 'PostgreSQL', status.postgres_version), row('pgvector_version', 'pgvector', status.pgvector_version), row('checked_at', 'Checked', moment(status.checked_at))]),
    group('index', 'Search by meaning', 'The state of the index', [
      row('indexed', 'Items in the index', String(index.indexed)),
      row('service', 'Embedding service', SERVICE[index.embedding_service] || index.embedding_service),
      row('model', 'Embedding model', index.embedding_model || 'None'),
      row('vector_size', 'Numbers in each vector', index.vector_size === null ? 'Not built yet' : String(index.vector_size)),
      row('last_reindex', 'Last indexed', last ? `${moment(last.at)}: ${last.error ? `stopped early (${last.error})` : `${plural(last.embedded, 'item')} embedded${last.failed_total ? `, ${last.failed_total} refused` : ''}, ${last.waiting_after ?? 0} still waiting`}, in ${last.seconds} s` : 'Not since the server started'),
    ]),
    group('email', 'Email', 'The mail server', mail && mail.enabled ? [row('mail-server', 'Mail server', sendingWords(mail))] : null),
    group('sign-in', 'Sign-in', 'The other ways to sign in', signInRows(signIn)),
    group('sizes', 'How much it holds', 'How much the project holds', SIZES.map(([name, label]) => row(name, label, String(status.counts[name])))),
  );
}

// -- the export ---------------------------------------------------------------------------------------

function exportSection() {
  const note = h('p', { class: 'quiet', id: 'export-note', role: 'status' });
  const problem = h('p', { class: 'problem', role: 'alert', id: 'export-problem' });
  const button = h(
    'button',
    {
      type: 'button',
      id: 'download-export',
      class: 'primary',
      onclick: async () => {
        button.disabled = true;
        problem.replaceChildren();
        note.textContent = 'Reading the whole graph…';
        try {
          const document_ = await readExport((pages) => {
            note.textContent = `Reading the whole graph… ${plural(pages, 'page')}`;
          });
          save(document_);
          note.textContent = `Downloaded: ${Object.entries(document_.counts).map(([name, count]) => `${count} ${name}`).join(', ')}.`;
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          note.textContent = '';
          refuse(problem, error.message);
        } finally {
          button.disabled = false;
        }
      },
    },
    'Download export',
  );
  return h(
    'section',
    { id: 'export', class: 'settings-section' },
    h('h2', {}, 'Download everything'),
    h('div', { class: 'actions' }, button),
    note,
    problem,
    h('p', { class: 'quiet', id: 'moving' }, 'Backup, restore and import are done from the command line by whoever runs the server. The backup and export guide has the commands.'),
  );
}

// Every page of the export put together, as the file the scripts write. It stops, saying so, if the
// graph changed while it was being read, because what was read would not fit together.
async function readExport(onPage) {
  const document_ = { format: null, version: null, counts: null };
  let after = null;
  let pages = 0;
  do {
    const page = await call('export_graph', { after, limit: 200 });
    document_.format = page.format;
    document_.version = page.version;
    if (page.counts) document_.counts = page.counts;
    if (page.section) (document_[page.section] ??= []).push(...page.items);
    after = page.next_after;
    pages += 1;
    onPage(pages);
  } while (after);
  for (const name of Object.keys(document_.counts || {})) {
    const read = (document_[name] ??= []).length;
    if (read !== document_.counts[name]) {
      throw new Refusal(`The graph changed while it was being read (${name}: ${document_.counts[name]} at the start, ${read} read). Download it again when nothing is writing.`);
    }
  }
  return document_;
}

function save(document_) {
  const text = JSON.stringify(document_, null, 2);
  const link = h('a', { href: URL.createObjectURL(new Blob([`${text}\n`], { type: 'application/json' })), download: `vrum-export-${new Date().toISOString().slice(0, 10)}.json` });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
}
