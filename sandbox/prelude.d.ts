// What a script can use besides `vrum`: the language, and a few helpers. The runner keeps exactly these
// globals and removes the rest. The app's declarations (the types and `declare const vrum`) follow this.
interface Console {
  log(...values: unknown[]): void;
  info(...values: unknown[]): void;
  warn(...values: unknown[]): void;
  error(...values: unknown[]): void;
  debug(...values: unknown[]): void;
}
declare const console: Console;
declare class VrumError extends Error {
  constructor(message: string);
}
declare function setTimeout(handler: (...args: any[]) => void, milliseconds?: number): number;
declare function clearTimeout(id?: number): void;
declare function queueMicrotask(callback: () => void): void;
declare function structuredClone<T>(value: T): T;
declare function atob(text: string): string;
declare function btoa(text: string): string;
