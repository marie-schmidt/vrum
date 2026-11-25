// What the server says to help a person review a change (M50), worked out into what the page draws. It draws nothing and asks for
// nothing: it reads the answers needs-details.js keeps and says, for a plan, what its three checks found and which of its items
// each finding is about, and, for a suggested move, how each moved item scores for the parent it would go under, which is the
// lowest, what is worth weighing, and whether the proposal is routine. Every word is the server's; the page adds none of its own
// about what is true. A score is evidence for a person, never an approval, and a check never stops anything.

import { plural } from './dom.js';
import { codeOf } from './labels.js';

// Git's similarity index is a whole number, rounded down: 0.924 is 92%, and a score can never read more than 100.
export const percentOf = (similarity) => Math.min(100, Math.max(0, Math.floor(similarity * 100)));

// A related item's percent in a check's words: the server says "looks 84% like S-38" with the same rounding.
export const nearPercent = (similarity) => Math.round(similarity * 100);

export function ordinal(number) {
  const tens = number % 100;
  const suffix = tens >= 10 && tens <= 20 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[number % 10] ?? 'th');
  return `${number}${suffix}`;
}

// How a check state is said. A word is always shown beside its sign: colour is never the only cue.
export const STATE_WORDS = { ok: 'OK', warning: 'Warning', failed: 'Failed', unavailable: 'Not checked' };

// What a kind of check says when it found nothing, in a few words, for the one quiet line of all that went well.
const OK_WORDS = { circle: 'no circles', unsized: 'every task sized', duplicate: 'no repeated work' };

// "No circles", "Every task sized", "No repeated work": the checks that passed, each as a short label (the page draws them as one quiet row).
// Nothing when none passed.
function okWords(checks) {
  const said = checks.filter((one) => one.state === 'ok').map((one) => OK_WORDS[one.kind] ?? one.words);
  if (!said.length) return null;
  return said.map((line) => line.charAt(0).toUpperCase() + line.slice(1));
}

// The head of a card says how much is to weigh: "1 failed, 2 warnings, not checked". Each count is of checks for a plan and of
// items for a move.
export function warnWords(warn) {
  return [warn.failed ? `${warn.failed} failed` : null, warn.warning ? plural(warn.warning, 'warning') : null, warn.unavailable ? 'not checked' : null].filter(Boolean).join(', ');
}

// The worst state there is: failed, then a warning, then not checked.
export const worstOf = (warn) => (warn.failed ? 'failed' : warn.warning ? 'warning' : 'unavailable');

const none = () => ({ failed: 0, warning: 0, unavailable: 0, total: 0 });

// The short words of a flag beside an item a check is about, and the server's whole sentence for it as its label.
function flagFor(check, finding) {
  if (check.kind === 'unsized') return { check: 'unsized', state: check.state, text: 'no size', label: finding.words };
  if (check.kind === 'duplicate') {
    const best = finding.related[0];
    return { check: 'duplicate', state: check.state, text: best ? `may repeat ${codeOf(best.ref)}` : 'may repeat existing work', label: finding.words };
  }
  return { check: 'circle', state: check.state, text: 'in a circle', label: finding.words };
}

// A plan: its checks, the ones that did not pass, and for each item of the plan the flags the findings put on it. `checks` is null
// for a plan that is decided, which has none to show.
export function planAssist(entry) {
  const checks = entry && entry.data ? entry.data.checks : null;
  const warn = none();
  const flags = new Map();
  if (!checks) return { checks: null, warn, flags, ok: null, open: [] };
  for (const check of checks) {
    if (check.state === 'ok') continue;
    warn[check.state] += 1;
    warn.total += 1;
    for (const finding of check.findings) {
      for (const ref of finding.items) {
        if (!flags.has(ref)) flags.set(ref, []);
        flags.get(ref).push(flagFor(check, finding));
      }
    }
  }
  return { checks, warn, flags, ok: okWords(checks), open: checks.filter((one) => one.state !== 'ok') };
}

// A suggested move: what each moved item scores, which are the lowest, what to weigh, and whether the proposal is routine. `proposal`
// is the proposal as `list_placements` gave it (null when it was not in the answer).
export function moveAssist(entry) {
  const reading = entry !== null && entry.state === 'loading' && !entry.data;
  const failed = entry !== null && entry.state === 'failed' ? entry.error : null;
  const proposal = entry && entry.data ? entry.data.proposal : null;
  const scores = new Map();
  const warn = none();
  let lowest = null;
  let everyScored = false;
  const marked = new Set();
  if (proposal && proposal.scores && proposal.scores.length) {
    for (const one of proposal.scores) {
      scores.set(one.ref, one);
      if (one.warning) {
        warn.warning += 1;
        warn.total += 1;
      }
    }
    const scored = proposal.scores.filter((one) => one.similarity !== null && one.similarity !== undefined);
    everyScored = scored.length === proposal.scores.length;
    if (scored.length) {
      const percents = scored.map((one) => percentOf(one.similarity));
      lowest = Math.min(...percents);
      // Marked only when there is more than one item and they do not all score the same.
      if (scored.length > 1 && Math.max(...percents) !== lowest) for (const one of scored) if (percentOf(one.similarity) === lowest) marked.add(one.ref);
    }
  }
  return {
    reading,
    failed,
    proposal,
    scores,
    warn,
    marked,
    // The digits after the R: the lowest score when every item has one.
    lowest: everyScored ? lowest : null,
    routine: Boolean(proposal && proposal.routine === true),
    why: proposal ? proposal.routine_why : null,
    note: proposal ? proposal.scores_note : null,
  };
}

// What a score says beside an item: "92%", and where the parent ranks when it is not first.
export const scoreLabel = (score) => `similarity ${percentOf(score.similarity)}%${score.rank ? `, ranks ${ordinal(score.rank)}` : ''}`;
