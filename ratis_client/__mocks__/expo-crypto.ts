// ratis_client/__mocks__/expo-crypto.ts

export enum CryptoDigestAlgorithm {
  SHA256 = "SHA-256",
}

let counter = 0;

export function randomUUID(): string {
  counter += 1;
  return `mock-uuid-${counter}`;
}

export async function digestStringAsync(
  _algo: CryptoDigestAlgorithm,
  input: string
): Promise<string> {
  // Not a real hash — tests just verify the value is passed through
  return `hashed(${input})`;
}

export function __reset() {
  counter = 0;
}
