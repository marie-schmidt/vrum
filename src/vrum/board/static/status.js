// Status and export: how the server is. It shows what `server_status` returns and nothing else about
// the server: the versions, how much of each kind of thing it holds, the state of the index and of
// the embedding service, and a download of the whole export. The database password is not here,
// because the server never says it. Import and backup are not done from a page.

import { Refusal, call } from './api.js';
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
  ['rules', 'Requirements'],
  ['decisions', 'Decisions'],
  ['facts', 'Facts'],
  ['events', 'Changes in the history'],
];

const SERVICE = {
  not_configured: 'No embedding service is set up',
  answering: 'Answering',
  unreachable: 'Not answering',
};

const MEANING = {
  used: 'In use',
  not_configured: 'Not set up (no embedding service)',
  index_not_ready: 'Not ready (not built yet, or built with another model)',
};

let root = null;
let generation = 0;
let timer = null;

export function mount(element) {
  root = element;
  put(
    root,
    h('div', { class: 'settings-page' }, [
      pageHeader('Status and export', 'How the server is, as the server says it. It never shows the database password.'),
      h('p', { class: 'problem', role: 'alert', id: 'status-problem' }),
      h('div', { id: 'status-body', class: 'status-grid' }, h('p', { class: 'loading', role: 'status' }, 'Asking the server…')),
      exportSection(),
      moveSection(),
    ]),
  );
  load();
  timer = setInterval(load, Math.max(10, store.info.poll_seconds * 2) * 1000);
}

export function unmount() {
  generation += 1;
  root = null;
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
    const status = await call('server_status', {});
    if (mine !== generation || !root) return;
    root.querySelector('#status-problem').replaceChildren();
    draw(status);
  } catch (error) {
    if (mine !== generation || !root) return;
    if (!(error instanceof Refusal)) throw error;
    refuse(root.querySelector('#status-problem'), error.message);
  }
}

const row = (name, label, value, extra) =>
  h('tr', { dataset: { name } }, h('th', { scope: 'row' }, label), h('td', { class: 'value' }, value), extra === undefined ? null : h('td', { class: 'quiet' }, extra));

// A table of facts that scrolls inside its own box when it is wider than the page.
const facts = (label, ...rows) => h('div', { class: 'table-scroll', role: 'region', tabindex: '0', 'aria-label': label }, h('table', { class: 'status-facts' }, h('tbody', {}, rows)));

function draw(status) {
  const index = status.index;
  const last = index.last_reindex;
  put(
    root.querySelector('#status-body'),
    h(
      'section',
      { id: 'versions', class: 'settings-section' },
      h('h2', {}, 'The server'),
      h('p', { class: 'ok status-line' }, icon('check', { size: 16 }), h('strong', {}, 'The server is running and can reach its database.')),
      facts('The server’s versions', row('vrum_version', 'Vrum', status.vrum_version), row('postgres_version', 'PostgreSQL', status.postgres_version), row('pgvector_version', 'pgvector', status.pgvector_version), row('checked_at', 'Read', moment(status.checked_at))),
    ),
    h('section', { id: 'sizes', class: 'settings-section' }, h('h2', {}, 'How much it holds'), facts('How much the project holds', SIZES.map(([name, label]) => row(name, label, String(status.counts[name]))))),
    h(
      'section',
      { id: 'index', class: 'settings-section' },
      h('h2', {}, 'Search by meaning'),
      facts(
        'The state of the index',
        row('meaning', 'The index', MEANING[index.meaning] || index.meaning, index.note || ''),
        row('waiting', 'Items waiting to be indexed', String(index.waiting_to_index), 'New or changed and not yet in the index: found by exact words at once, by meaning once indexed.'),
        row('indexed', 'Items in the index', String(index.indexed), ''),
        row('service', 'Embedding service', SERVICE[index.embedding_service] || index.embedding_service, ''),
        row('model', 'Embedding model', index.embedding_model || 'None', ''),
        row('vector_size', 'Numbers in each vector', index.vector_size === null ? 'Not built yet' : String(index.vector_size), ''),
        row('last_reindex', 'The last time the index was brought up to date', last ? `${moment(last.at)}: ${last.error ? `stopped early (${last.error})` : `${plural(last.embedded, 'item')} embedded${last.failed_total ? `, ${last.failed_total} refused` : ''}, ${last.waiting_after ?? 0} still waiting`}, in ${last.seconds} s` : 'Not since the server started', ''),
      ),
    ),
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
    'Download the full export',
  );
  return h(
    'section',
    { id: 'export', class: 'settings-section' },
    h('h2', {}, 'Download everything'),
    h('p', {}, 'One file with every epic, story, task, link, requirement, decision, fact and the whole history, to keep or to load into an empty project. It is the same as the export an agent reads.'),
    h('div', { class: 'actions' }, button),
    note,
    problem,
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

function moveSection() {
  return h(
    'section',
    { id: 'moving', class: 'settings-section' },
    h('h2', {}, 'Backup, restore and import'),
    h('p', {}, 'These are done from the command line, not from a page, because they replace or fill a whole database and a mistake in a browser tab should not be able to do that. See docs/backup-and-export.md for the commands: ', h('code', {}, './scripts/backup.sh'), ', ', h('code', {}, './scripts/restore.sh'), ' and ', h('code', {}, 'scripts/import_graph.py'), '.'),
  );
}
