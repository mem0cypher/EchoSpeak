declare module "vitest" {
  export function describe(name: string, fn: () => void): void;
  export function it(name: string, fn: () => void): void;
  export function expect<T = unknown>(value: T): {
    toEqual(expected: unknown): void;
    toBe(expected: unknown): void;
    toBeNull(): void;
    toBeCloseTo(expected: number, precision?: number): void;
  };
}
