export const STORE_ACCENT_COLORS = [
  '#FB7185', // coral
  '#FFB800', // gold
  '#A78BFA', // royal violet light
  '#EF4444', // red
  '#FB923C', // orange
] as const;

export function getStoreAccent(storeName: string): string {
  const hash = storeName
    .split('')
    .reduce((acc, c) => acc + c.charCodeAt(0), 0);
  return STORE_ACCENT_COLORS[hash % STORE_ACCENT_COLORS.length];
}
