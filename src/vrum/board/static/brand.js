// The vrum wordmark, drawn inline from the shapes of brand/vrum-logo.svg so that it follows the
// theme: the letters take the colour of the text around them and the vector in the V takes the
// accent. (An image file cannot change colour with the theme.)

import { s } from './dom.js';

export function wordmark(height = 28) {
  const width = Math.round((height * 519) / 180);
  return s(
    'svg',
    { class: 'wordmark', viewBox: '-40 -40 519 180', width, height, role: 'img', 'aria-label': 'vrum', focusable: 'false' },
    s(
      'g',
      { transform: 'skewX(-9)' },
      s('polygon', { class: 'wordmark-v', points: '0,0 22,0 46.5,57 71,0 96,0 53,100 43,100' }),
      s('polygon', { class: 'wordmark-vector', points: '71,0 96,0 53,100 43,100 35.5,82.6' }),
      s('path', {
        class: 'wordmark-rum',
        d: 'M123,100 V11 H159 M185,0 V61 A28,28 0 0 0 213,89 H247 V0 M309,100 V11 H405 A28,28 0 0 1 433,39 V100 M371,100 V11 M343,11 A28,28 0 0 1 371,39',
      }),
    ),
  );
}
