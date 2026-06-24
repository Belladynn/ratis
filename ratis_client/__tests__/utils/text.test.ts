// ratis_client/__tests__/utils/text.test.ts
//
// Test the toDisplayCase normalization helper used to UPPERCASE OCR-derived
// strings before display (decision 2026-04-30, see utils/text.ts comment).

import { toDisplayCase } from '../../utils/text';

describe('toDisplayCase', () => {
  it('uppercases mixed-case OCR output', () => {
    expect(toDisplayCase('InteRmaRché')).toBe('INTERMARCHÉ');
  });

  it('keeps already-uppercase strings unchanged', () => {
    expect(toDisplayCase('MONOPRIX')).toBe('MONOPRIX');
  });

  it('preserves digits and spaces', () => {
    expect(toDisplayCase('18 ter rue de bezons')).toBe('18 TER RUE DE BEZONS');
  });

  it('preserves accented characters', () => {
    expect(toDisplayCase('épicerie')).toBe('ÉPICERIE');
  });

  it('preserves hyphens and apostrophes', () => {
    expect(toDisplayCase("saint-denis-l'épicerie")).toBe("SAINT-DENIS-L'ÉPICERIE");
  });

  it('returns empty string unchanged', () => {
    expect(toDisplayCase('')).toBe('');
  });

  it('returns whitespace-only string unchanged', () => {
    expect(toDisplayCase('   ')).toBe('   ');
  });
});
