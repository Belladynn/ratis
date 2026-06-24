import { getStoreAccent, STORE_ACCENT_COLORS } from '@/utils/store-accent';

describe('getStoreAccent', () => {
  it('returns a color from STORE_ACCENT_COLORS', () => {
    const color = getStoreAccent('Carrefour');
    expect(STORE_ACCENT_COLORS).toContain(color);
  });

  it('returns same color for same store name (deterministic)', () => {
    expect(getStoreAccent('Monoprix')).toBe(getStoreAccent('Monoprix'));
    expect(getStoreAccent('Leclerc')).toBe(getStoreAccent('Leclerc'));
  });

  it('returns different colors for different hashes', () => {
    const names = ['Carrefour', 'Monoprix', 'Leclerc', 'Auchan', 'Lidl'];
    const colors = new Set(names.map(getStoreAccent));
    expect(colors.size).toBeGreaterThan(1);
  });

  it('handles empty string', () => {
    expect(STORE_ACCENT_COLORS).toContain(getStoreAccent(''));
  });
});
