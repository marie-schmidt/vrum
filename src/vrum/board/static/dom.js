// Building pages out of elements. Text is always put in as text, never as HTML, so whatever a
// title or a note contains is shown as it is and nothing in it can run.

const SVG = 'http://www.w3.org/2000/svg';

function fill(el, props, children) {
  for (const [name, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (name === 'class') el.setAttribute('class', value);
    else if (name === 'dataset') {
      // A value that is not there leaves the attribute off, and is not written as the word "null".
      for (const [key, one] of Object.entries(value)) if (one !== null && one !== undefined) el.dataset[key] = one;
    }
    // Styles are set from script, because the page's policy does not allow a style attribute.
    else if (name === 'css') Object.assign(el.style, value);
    else if (name.startsWith('on')) el.addEventListener(name.slice(2).toLowerCase(), value);
    else if (name === 'value' || name === 'checked' || name === 'disabled' || name === 'open') {
      // Set after the children, so that a select can find its option.
      continue;
    } else el.setAttribute(name, value === true ? '' : String(value));
  }
  const add = (child) => {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) child.forEach(add);
    else el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  };
  children.forEach(add);
  if (props) {
    if ('value' in props && props.value !== null && props.value !== undefined) el.value = props.value;
    if (props.checked) el.checked = true;
    if (props.open) el.open = true;
    if (props.disabled) el.disabled = true;
  }
  return el;
}

export function h(tag, props, ...children) {
  return fill(document.createElement(tag), props, children);
}

export function s(tag, props, ...children) {
  return fill(document.createElementNS(SVG, tag), props, children);
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

// Replaces what an element holds with these children, leaving out the empty ones (a bare append
// would write "null") and opening up lists.
export function put(el, ...children) {
  el.replaceChildren();
  const add = (child) => {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) child.forEach(add);
    else el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  };
  children.forEach(add);
  return el;
}

export function announce(text) {
  const live = document.getElementById('live');
  if (live) live.textContent = text;
}

// The day as written on a card: 2026-11-30.
export function day(value) {
  return value ? String(value).slice(0, 10) : '';
}

// A moment as written on a card, in the person's own time: "2026-11-18, 6:57 PM". The day is
// written the way a day alone always is (see `day`), so a card that says "Due 2026-11-30" and
// "held until 2026-11-18, 6:57 PM" uses one form, and every browser writes it the same way (some
// write English as "Nov 18, 2026 at 6:57 PM"). The time follows the person's own clock settings.
export function moment(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const two = (number) => String(number).padStart(2, '0');
  const clock = date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }).replace(/\s/g, '\u00a0');
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}, ${clock}`;
}

export function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many || one + 's'}`;
}
