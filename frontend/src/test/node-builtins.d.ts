// Minimal typings for the few Node built-ins used by tests that run in the node environment (infraModels.test.ts).
// The project does not install @types/node; these declarations cover only what those tests call.
declare module "node:fs" {
  export function readFileSync(path: string): { buffer: ArrayBuffer; byteOffset: number; byteLength: number };
  export function existsSync(path: string): boolean;
}

declare module "node:path" {
  export function join(...parts: string[]): string;
}

declare const process: { cwd(): string };
