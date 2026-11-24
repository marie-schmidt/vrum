// Email nudges (M48), the section of the Account page where a person asks to be mailed when something waits for them: for each project
// they are in, a row with the five kinds of news a person may ask for (a question or approval, a drafted plan, a suggested move, work
// that was blocked, a claim that ran out), saved when that row's Save is pressed. The mail says the kind of news and which story or task,
// with a link to the board, and never anything anyone wrote; it goes only to this person, at most ten an hour, and stops when they leave
// the project.
//
// What can be asked for, whether the server can send email at all and whether this person's address is confirmed all come from the
// server (`nudges`), and so does every refusal, which is shown in its words. Without email the section says so in one sentence and has no
// control; with an address that is not confirmed it says to confirm it first and the boxes are there but cannot be used. Any member of a
// project may ask, a viewer too.

import { Refusal, Stale, nudges, projects, setNudges } from './api.js';
import { notify } from './ctx.js';
import { h, put } from './dom.js';
import { refuse } from './forms.js';
import { icon } from './icons.js';
import { roleChip } from './labels.js';

let generation = 0;

export function forgetNudges() {
  generation += 1;
}

// The setting, drawn now with "Reading…" and filled in when the server has answered. It is one setting of the account like the others (its
// title on the left, the projects on the right), so the page keeps its single section. `toEmail()` takes the person to the Email setting,
// where an address is confirmed.
export function nudgesSection(toEmail) {
  const body = h('div', { class: 'nudges-body', id: 'nudges-body' }, h('p', { class: 'loading', role: 'status' }, 'Reading your email settings…'));
  const element = h('div', { class: 'setting', id: 'account-nudges' }, h('div', { class: 'setting-text' }, h('p', { class: 'setting-title', id: 'account-nudges-title' }, 'Email nudges')), h('div', { class: 'setting-control', role: 'group', 'aria-labelledby': 'account-nudges-title' }, body));
  load(++generation, body, toEmail);
  return element;
}

async function load(mine, body, toEmail) {
  let answer;
  let ofMine;
  try {
    [answer, ofMine] = await Promise.all([nudges(), projects()]);
  } catch (error) {
    if (mine !== generation || error instanceof Stale) return;
    if (!(error instanceof Refusal)) throw error;
    const failed = h('p', { class: 'problem', role: 'alert' });
    refuse(failed, error.message);
    put(body, failed);
    return;
  }
  if (mine !== generation) return;
  draw(body, answer, ofMine, toEmail);
}

function draw(body, answer, list, toEmail) {
  if (!answer.mail) {
    put(body, h('p', { id: 'nudges-off', class: 'help' }, 'Email is not set up on this server, so nudges cannot be sent.'));
    return;
  }
  const usable = answer.email_confirmed === true;
  const asked = new Map(answer.nudges.map((one) => [one.project, one.events]));
  put(
    body,
    h('p', { class: 'help nudges-about' }, 'Ask to be emailed when something waits for you in a project. The email says what kind of news it is and which story or task, with a link to the board, and nothing anyone wrote. At most ten an hour.'),
    usable
      ? null
      : h(
          'p',
          { id: 'nudges-unconfirmed', class: 'notice nudges-unconfirmed', role: 'status' },
          icon('alert', { size: 16 }),
          h('span', {}, 'Confirm your email address first.'),
          h('button', { type: 'button', id: 'nudges-confirm', class: 'small', onclick: toEmail }, 'Go to the Email setting'),
        ),
    h('div', { class: 'nudges', id: 'nudges' }, list.map((project) => projectRow(project, answer.kinds, asked.get(project.id) || [], usable))),
  );
}

// One project: its name and role, the kinds as boxes with the server's sentence for each, and its own Save.
function projectRow(project, kinds, chosen, usable) {
  const boxes = Object.entries(kinds).map(([kind, words]) => {
    const box = h('input', { type: 'checkbox', id: `nudge-${project.id}-${kind}`, name: 'kind', value: kind, checked: chosen.includes(kind), disabled: !usable });
    return { kind, box, element: h('label', { class: 'check nudge-kind', for: box.id }, box, h('span', {}, words)) };
  });
  const problem = h('p', { class: 'problem', role: 'alert' });
  const said = h('p', { class: 'quiet nudge-saved', role: 'status' });
  const save = h('button', { type: 'submit', class: 'small primary nudge-save', 'aria-label': `Save the email nudges for ${project.name}`, disabled: !usable }, 'Save');
  const form = h(
    'form',
    {
      class: 'nudge',
      novalidate: true,
      dataset: { project: project.id },
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        said.replaceChildren();
        save.disabled = true;
        try {
          const made = await setNudges(project.id, boxes.filter((one) => one.box.checked).map((one) => one.box.value));
          put(said, made.events.length ? `Saved. You will be emailed about ${made.events.length === 1 ? 'one kind of news' : `${made.events.length} kinds of news`} in this project.` : 'Saved. You will not be emailed about this project.');
          notify(`Saved your email nudges for “${project.name}”.`);
        } catch (error) {
          if (error instanceof Stale) return;
          if (!(error instanceof Refusal)) throw error;
          refuse(problem, error.message);
        } finally {
          save.disabled = !usable;
        }
      },
    },
    h(
      'fieldset',
      { class: 'nudge-kinds' },
      h('legend', { class: 'nudge-project' }, h('span', { class: 'nudge-name' }, project.name), roleChip(project.role)),
      boxes.map((one) => one.element),
    ),
    h('div', { class: 'nudge-actions' }, save, said),
    problem,
  );
  return form;
}
