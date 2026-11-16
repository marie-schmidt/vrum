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
