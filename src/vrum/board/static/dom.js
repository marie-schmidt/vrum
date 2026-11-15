// Building pages out of elements. Text is always put in as text, never as HTML, so whatever a
// title or a note contains is shown as it is and nothing in it can run.

const SVG = 'http://www.w3.org/2000/svg';

function fill(el, props, children) {
  for (const [name, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (name === 'class') el.setAttribute('class', value);
    else if (name === 'dataset') Object.assign(el.dataset, value);
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

export function announce(text) {
  const live = document.getElementById('live');
  if (live) live.textContent = text;
}

// The day as written on a card: 2026-11-30.
export function day(value) {
  return value ? String(value).slice(0, 10) : '';
}

// A moment as written on a card, in the person's own time.
export function moment(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many || one + 's'}`;
}
