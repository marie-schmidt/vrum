// Days as whole numbers, so that dates can be added to and compared without time zones. A day is
// the number of days since 1970-01-01, and "today" is the UTC day, which is the day the database
// counts overdue work by.

const DAY_MS = 86400000;

export const toDay = (iso) => {
  const [year, month, date] = String(iso).slice(0, 10).split('-').map(Number);
  return Date.UTC(year, month - 1, date) / DAY_MS;
};

export const fromDay = (day) => new Date(day * DAY_MS).toISOString().slice(0, 10);

export const todayDay = () => Math.floor(Date.now() / DAY_MS);

// 0 = Sunday ... 6 = Saturday.
export const weekday = (day) => (((day + 4) % 7) + 7) % 7;

export const monthStart = (day) => {
  const when = new Date(day * DAY_MS);
  return Date.UTC(when.getUTCFullYear(), when.getUTCMonth(), 1) / DAY_MS;
};

export const nextMonth = (day) => {
  const when = new Date(day * DAY_MS);
  return Date.UTC(when.getUTCFullYear(), when.getUTCMonth() + 1, 1) / DAY_MS;
};

export const monthName = (day) =>
  new Date(day * DAY_MS).toLocaleString('en', { month: 'short', timeZone: 'UTC' });

export const yearOf = (day) => new Date(day * DAY_MS).getUTCFullYear();
export const monthOf = (day) => new Date(day * DAY_MS).getUTCMonth();
export const dayOfMonth = (day) => new Date(day * DAY_MS).getUTCDate();

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

export const weekdayName = (day) => WEEKDAYS[weekday(day)];
export const longMonthName = (day) => MONTHS[monthOf(day)];

// The same day of the month `count` months on (a negative count goes back), or the last day of that month when
// it has no such day: one month after 31 January is 28 February.
export const addMonths = (day, count) => {
  const when = new Date(day * DAY_MS);
  const first = Date.UTC(when.getUTCFullYear(), when.getUTCMonth() + count, 1) / DAY_MS;
  return Math.min(first + when.getUTCDate() - 1, nextMonth(first) - 1);
};

// The day a text written as YYYY-MM-DD names, or null when it is anything else (including a day that is not on
// the calendar, such as 2026-02-30).
export const parseDay = (text) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
  const day = toDay(text);
  return Number.isFinite(day) && fromDay(day) === text ? day : null;
};
