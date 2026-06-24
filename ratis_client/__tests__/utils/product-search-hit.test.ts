// __tests__/utils/product-search-hit.test.ts
//
// Pure-helper tests for the search-hit secondary-line composition (wave
// 9, PO « pomme de terre » duplicate disambig).

import {
  composeSearchHitSecondary,
  isFrenchHit,
  isOrganicHit,
} from '@/utils/product-search-hit';

const BASE = {
  ean: '3017620421002',
  name: 'Pomme de terre',
  brands: null,
  quantity: null,
  categories_tags: null,
  labels_tags: null,
  origins_tags: null,
  source: 'off',
} as const;

describe('isOrganicHit', () => {
  it('returns false when labels_tags is null or empty', () => {
    expect(isOrganicHit({ labels_tags: null })).toBe(false);
    expect(isOrganicHit({ labels_tags: [] })).toBe(false);
  });

  it('matches en:organic / fr:bio / en:eu-organic / fr:agriculture-biologique', () => {
    expect(isOrganicHit({ labels_tags: ['en:organic'] })).toBe(true);
    expect(isOrganicHit({ labels_tags: ['fr:bio'] })).toBe(true);
    expect(isOrganicHit({ labels_tags: ['en:eu-organic'] })).toBe(true);
    expect(
      isOrganicHit({ labels_tags: ['fr:agriculture-biologique'] }),
    ).toBe(true);
  });

  it('is case-insensitive', () => {
    expect(isOrganicHit({ labels_tags: ['EN:Organic'] })).toBe(true);
  });

  it('does NOT match partial / derived tags', () => {
    expect(
      isOrganicHit({ labels_tags: ['en:organic-farming-something'] }),
    ).toBe(false);
  });
});

describe('isFrenchHit', () => {
  it('returns false when origins_tags is null or empty', () => {
    expect(isFrenchHit({ origins_tags: null })).toBe(false);
    expect(isFrenchHit({ origins_tags: [] })).toBe(false);
  });

  it('matches en:france / fr:france / en:made-in-france', () => {
    expect(isFrenchHit({ origins_tags: ['en:france'] })).toBe(true);
    expect(isFrenchHit({ origins_tags: ['fr:france'] })).toBe(true);
    expect(isFrenchHit({ origins_tags: ['en:made-in-france'] })).toBe(true);
  });

  it('matches when a French signal appears alongside other origins', () => {
    expect(
      isFrenchHit({
        origins_tags: ['en:european-union', 'en:france'],
      }),
    ).toBe(true);
  });

  it('does NOT match derived sub-tags', () => {
    expect(
      isFrenchHit({ origins_tags: ['en:france-metropolitaine'] }),
    ).toBe(false);
  });
});

describe('composeSearchHitSecondary', () => {
  it('returns null when all discriminating signals are absent', () => {
    expect(composeSearchHitSecondary(BASE)).toBeNull();
  });

  it('renders brand only when only brand is present', () => {
    expect(
      composeSearchHitSecondary({ ...BASE, brands: 'Carrefour' }),
    ).toBe('Carrefour');
  });

  it('renders brand · quantity when both are present (no flag, no bio)', () => {
    expect(
      composeSearchHitSecondary({
        ...BASE,
        brands: 'Carrefour',
        quantity: '1 kg',
      }),
    ).toBe('Carrefour · 1 kg');
  });

  it('renders the French flag when origins_tags is en:france', () => {
    expect(
      composeSearchHitSecondary({
        ...BASE,
        brands: 'Carrefour',
        quantity: '1 kg',
        origins_tags: ['en:france'],
      }),
    ).toBe('Carrefour · 1 kg · 🇫🇷');
  });

  it('renders the BIO leaf when labels_tags carries en:organic', () => {
    expect(
      composeSearchHitSecondary({
        ...BASE,
        brands: 'Bio Village',
        quantity: '500 g',
        origins_tags: ['en:france'],
        labels_tags: ['en:organic'],
      }),
    ).toBe('Bio Village · 500 g · 🇫🇷 · 🌱');
  });

  it('renders quantity-only line when there is no brand', () => {
    expect(
      composeSearchHitSecondary({ ...BASE, quantity: '1 L' }),
    ).toBe('1 L');
  });

  it('renders flag + leaf without brand or quantity', () => {
    expect(
      composeSearchHitSecondary({
        ...BASE,
        origins_tags: ['en:france'],
        labels_tags: ['fr:bio'],
      }),
    ).toBe('🇫🇷 · 🌱');
  });

  it('ignores whitespace-only brand / quantity', () => {
    expect(
      composeSearchHitSecondary({
        ...BASE,
        brands: '  ',
        quantity: '\t',
      }),
    ).toBeNull();
  });
});
