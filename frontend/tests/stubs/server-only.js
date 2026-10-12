// Vitest stand-in for the `server-only` marker package, which deliberately
// throws when imported outside a React Server Components build. Tests run
// the server modules directly in Node, which is exactly where they belong.
export {};
