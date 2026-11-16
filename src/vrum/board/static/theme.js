// Decides the theme before the page is drawn. It is a plain script in the head (not a module), so it
// runs before the first paint and the page never flashes the wrong colours. It reads only the
// person's own choice (Light, Dark or System, in this browser) and the operating system's setting;
// no credential is ever kept with it, and signing out does not clear it.
//
// Light and Dark override the system. System follows it, also while the page is open.
(function () {
  var KEY = 'vrum.theme';
  var root = document.documentElement;
  var query = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  var memory = null; // the choice, when this browser will not store it

  function choice() {
    var saved = memory;
    try {
      saved = window.localStorage.getItem(KEY) || memory;
    } catch (error) {
      saved = memory;
    }
    return saved === 'light' || saved === 'dark' ? saved : 'system';
  }

  function apply() {
    var chosen = choice();
    var dark = chosen === 'dark' || (chosen === 'system' && query !== null && query.matches);
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
    root.setAttribute('data-theme-choice', chosen);
    window.dispatchEvent(new CustomEvent('vrum-theme', { detail: { choice: chosen, theme: dark ? 'dark' : 'light' } }));
  }

  function set(next) {
    memory = next === 'system' ? null : next;
    try {
      if (next === 'system') window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, next);
    } catch (error) {
      // Private browsing may refuse to store it: the choice then lasts until the page closes.
    }
    apply();
  }

  window.vrumTheme = { choice: choice, set: set };
  apply();
  if (query !== null) {
    if (query.addEventListener) query.addEventListener('change', apply);
    else if (query.addListener) query.addListener(apply);
  }
  window.addEventListener('storage', function (event) {
    if (event.key === KEY || event.key === null) apply();
  });
})();
