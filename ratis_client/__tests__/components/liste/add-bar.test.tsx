// __tests__/components/liste/add-bar.test.tsx
//
// Restored at chunk 4 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// Extended in wave 4 (Bug 3) with the search-autocomplete dropdown
// scenarios.

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => {
  const RN = require('react-native');
  const RnReact = require('react');
  return {
    LinearGradient: ({ children, ...props }: { children?: React.ReactNode }) =>
      RnReact.createElement(RN.View, props, children),
  };
});

const mockUseProductSearch = jest.fn();
jest.mock('@/hooks/use-product-search', () => ({
  useProductSearch: (...args: unknown[]) => mockUseProductSearch(...args),
}));
// Wave 13 (PO ticket 2026-05-14 follow-up) — empty-state hits come
// from the dedicated ``useDefaultSuggestions`` hook (tier-composed
// server-side) instead of the legacy ``defaultMode`` branch of
// ``useProductSearch``. AddBar always-mounts both hooks ; ``trimmed
// === ''`` picks which one feeds the dropdown.
const mockUseDefaultSuggestions = jest.fn();
jest.mock('@/hooks/use-default-suggestions', () => ({
  useDefaultSuggestions: (...args: unknown[]) =>
    mockUseDefaultSuggestions(...args),
}));

import { AddBar } from '@/components/liste/add-bar';

describe('AddBar', () => {
  const baseProps = {
    onSubmit: jest.fn(),
    onPressSuggestions: jest.fn(),
    onPressTemplates: jest.fn(),
    onPressVoice: jest.fn(),
  };

  beforeEach(() => {
    baseProps.onSubmit.mockReset();
    baseProps.onPressSuggestions.mockReset();
    baseProps.onPressTemplates.mockReset();
    baseProps.onPressVoice.mockReset();
    mockUseProductSearch.mockReset();
    mockUseProductSearch.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
    mockUseDefaultSuggestions.mockReset();
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
  });

  it('renders the placeholder text from i18n', () => {
    const { getByPlaceholderText } = render(<AddBar {...baseProps} />);
    expect(getByPlaceholderText('Ajouter un produit…')).toBeTruthy();
  });

  it('calls onPressSuggestions when the 💡 button is pressed', () => {
    const { getByTestId } = render(<AddBar {...baseProps} />);
    fireEvent.press(getByTestId('liste-add-bar-suggestions'));
    expect(baseProps.onPressSuggestions).toHaveBeenCalledTimes(1);
  });

  it('calls onPressTemplates when the ✨ button is pressed', () => {
    const { getByTestId } = render(<AddBar {...baseProps} />);
    fireEvent.press(getByTestId('liste-add-bar-templates'));
    expect(baseProps.onPressTemplates).toHaveBeenCalledTimes(1);
  });

  it('calls onPressVoice when the 🎤 button is pressed', () => {
    const { getByTestId } = render(<AddBar {...baseProps} />);
    fireEvent.press(getByTestId('liste-add-bar-voice'));
    expect(baseProps.onPressVoice).toHaveBeenCalledTimes(1);
  });

  it('does NOT render the « + » submit button (wave 7 — removed per PO directive)', () => {
    // PO directive 2026-05-13 follow-up : « J'aurai bien enlevé le + en
    // vrai et juste appuyer sur l'item dans la liste déroulante de la
    // recherche ça l'ajoute directement ». The only add-to-list path is
    // now tapping a dropdown hit (or keyboard Enter as fallthrough).
    const { queryByTestId } = render(<AddBar {...baseProps} />);
    expect(queryByTestId('liste-add-bar-submit')).toBeNull();
  });
});

// Bug 3 (wave 4 — PO ticket 2026-05-12) — search autocomplete dropdown.
describe('AddBar — wave-4 search autocomplete (Bug 3)', () => {
  const HIT = {
    ean: '3017620420001',
    name: 'Lait demi-écrémé 1L',
    brands: 'Lactel',
    quantity: null,
    categories_tags: null,
    labels_tags: null,
    origins_tags: null,
    source: 'off',
  };

  const baseProps = {
    onSubmit: jest.fn(),
    onPressSuggestions: jest.fn(),
    onPressTemplates: jest.fn(),
    onPressVoice: jest.fn(),
  };

  beforeEach(() => {
    baseProps.onSubmit.mockReset();
    mockUseProductSearch.mockReset();
    mockUseDefaultSuggestions.mockReset();
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
  });

  it('does not render the dropdown when the input is not focused (empty + blurred)', () => {
    mockUseProductSearch.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
    const { queryByTestId } = render(<AddBar {...baseProps} />);
    expect(queryByTestId('liste-add-bar-dropdown')).toBeNull();
  });

  it('renders search hits when the user has typed and the input is focused', () => {
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT] },
      isFetching: false,
    });
    const { getByTestId } = render(<AddBar {...baseProps} />);
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    fireEvent.changeText(input, 'lait');
    expect(getByTestId('liste-add-bar-dropdown')).toBeTruthy();
    expect(getByTestId(`liste-add-bar-hit-${HIT.ean}`)).toBeTruthy();
  });

  it('fires onSelectHit with the picked hit and clears the input', () => {
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT] },
      isFetching: false,
    });
    const onSelectHit = jest.fn();
    const { getByTestId } = render(
      <AddBar {...baseProps} onSelectHit={onSelectHit} />,
    );
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    fireEvent.changeText(input, 'lait');
    // ``onPressIn`` fires on touch-down (see add-bar.tsx hit row).
    fireEvent(getByTestId(`liste-add-bar-hit-${HIT.ean}`), 'pressIn');
    expect(onSelectHit).toHaveBeenCalledWith(HIT);
    expect(input.props.value).toBe('');
  });

  // Regression — PO ticket 2026-05-12 « quand je choisi un dans la
  // liste, ça ne l'ajoute pas ». Root cause : the dropdown row
  // previously fired on ``onPress`` (touch-up), but the TextInput's
  // ``onBlur`` schedules ``setFocused(false)`` first and many real-
  // device taps last longer than the 150 ms timeout that used to
  // protect us — the dropdown unmounted before the press landed and
  // the handler was never invoked. Switching to ``onPressIn`` (touch-
  // down) guarantees the handler reaches the parent BEFORE any blur
  // cascade can tear the row down. This test simulates that exact
  // cascade : focus → type → blur (which would schedule unmount) →
  // press the row. With ``onPress`` it would silently no-op ; with
  // ``onPressIn`` we still capture the hit.
  it('captures the hit even when blur cascade fires before the press lands', () => {
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT] },
      isFetching: false,
    });
    const onSelectHit = jest.fn();
    const { getByTestId } = render(
      <AddBar {...baseProps} onSelectHit={onSelectHit} />,
    );
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    fireEvent.changeText(input, 'lait');
    const row = getByTestId(`liste-add-bar-hit-${HIT.ean}`);
    // Blur fires synchronously when the user touches an element that
    // takes focus away from the TextInput.
    fireEvent(input, 'blur');
    // The press still resolves before the deferred setFocused(false)
    // runs (no fake timers advanced), because onPressIn fires on
    // touch-down.
    fireEvent(row, 'pressIn');
    expect(onSelectHit).toHaveBeenCalledWith(HIT);
  });

  it('shows the empty state when search returns no results', () => {
    mockUseProductSearch.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
    const { getByTestId, getByText } = render(<AddBar {...baseProps} />);
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    fireEvent.changeText(input, 'xyz');
    expect(getByTestId('liste-add-bar-empty')).toBeTruthy();
    expect(getByText('Aucun produit trouvé')).toBeTruthy();
  });
});

// Wave 6 (PO ticket 2026-05-13 Issue 3) — « + » button picks the first
// dropdown hit instead of opening the legacy suggestions sheet.
describe('AddBar — wave-6 « + » picks first hit (Issue 3)', () => {
  const HIT_A = {
    ean: '3017620420001',
    name: 'Lait demi-écrémé 1L',
    brands: 'Lactel',
    quantity: null,
    categories_tags: null,
    labels_tags: null,
    origins_tags: null,
    source: 'off',
  };
  const HIT_B = {
    ean: '3017620420002',
    name: 'LAIT entier 1L',
    brands: null,
    quantity: null,
    categories_tags: null,
    labels_tags: null,
    origins_tags: null,
    source: 'off',
  };

  const baseProps = {
    onSubmit: jest.fn(),
    onPressSuggestions: jest.fn(),
    onPressTemplates: jest.fn(),
    onPressVoice: jest.fn(),
  };

  beforeEach(() => {
    baseProps.onSubmit.mockReset();
    baseProps.onPressSuggestions.mockReset();
    baseProps.onPressTemplates.mockReset();
    baseProps.onPressVoice.mockReset();
    mockUseProductSearch.mockReset();
    mockUseDefaultSuggestions.mockReset();
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
  });

  it('picks the FIRST hit when the keyboard return key is pressed (onSubmitEditing)', () => {
    // Wave 7 — the « + » button was removed ; the keyboard Enter / Done
    // key remains as a power-user shortcut for « pick first hit ».
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT_A] },
      isFetching: false,
    });
    const onSelectHit = jest.fn();
    const { getByTestId } = render(
      <AddBar {...baseProps} onSelectHit={onSelectHit} />,
    );
    const input = getByTestId('liste-add-bar-input');
    fireEvent.changeText(input, 'lait');
    fireEvent(input, 'submitEditing');
    expect(onSelectHit).toHaveBeenCalledWith(HIT_A);
    expect(baseProps.onSubmit).not.toHaveBeenCalled();
  });

  it('clears the input after keyboard-Enter picks a hit', () => {
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT_A] },
      isFetching: false,
    });
    const onSelectHit = jest.fn();
    const { getByTestId } = render(
      <AddBar {...baseProps} onSelectHit={onSelectHit} />,
    );
    const input = getByTestId('liste-add-bar-input');
    fireEvent.changeText(input, 'lait');
    fireEvent(input, 'submitEditing');
    expect(input.props.value).toBe('');
  });

  it('falls back to onSubmit when keyboard-Enter is pressed without any hits', () => {
    // No hits loaded yet (debounce hasn't fired or search returned
    // empty). The legacy onSubmit fallback fires so the parent can
    // still react to the raw text — parent wires it to a no-op.
    mockUseProductSearch.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
    const onSelectHit = jest.fn();
    const { getByTestId } = render(
      <AddBar {...baseProps} onSelectHit={onSelectHit} />,
    );
    const input = getByTestId('liste-add-bar-input');
    fireEvent.changeText(input, 'eau');
    fireEvent(input, 'submitEditing');
    expect(onSelectHit).not.toHaveBeenCalled();
    expect(baseProps.onSubmit).toHaveBeenCalledWith('eau');
  });
});

// Wave 13 (PO ticket 2026-05-14 follow-up) — default suggestions on
// focus, tier-composed server-side via the dedicated
// ``useDefaultSuggestions`` hook. PO directive : « quand on ouvre la
// barre AddBar, on doit voir des suggestions avant même de taper quoi
// que ce soit ». Replaces the wave-12 ``defaultMode`` branch of
// ``useProductSearch``.
describe('AddBar — wave-13 default suggestions on focus', () => {
  const HIT_A = {
    ean: '3017620420001',
    name: 'Ail des ours',
    brands: null,
    quantity: null,
    categories_tags: null,
    labels_tags: null,
    origins_tags: null,
    source: 'off',
  };
  const HIT_B = {
    ean: '3017620420002',
    name: 'Banane bio',
    brands: null,
    quantity: null,
    categories_tags: null,
    labels_tags: null,
    origins_tags: null,
    source: 'off',
  };

  const baseProps = {
    onSubmit: jest.fn(),
    onPressSuggestions: jest.fn(),
    onPressTemplates: jest.fn(),
    onPressVoice: jest.fn(),
  };

  beforeEach(() => {
    mockUseProductSearch.mockReset();
    mockUseProductSearch.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
    mockUseDefaultSuggestions.mockReset();
    baseProps.onSubmit.mockReset();
    baseProps.onPressSuggestions.mockReset();
    baseProps.onPressTemplates.mockReset();
    baseProps.onPressVoice.mockReset();
  });

  it('opens the dropdown on focus with empty input and shows the default hits', () => {
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [HIT_A, HIT_B] },
      isFetching: false,
    });
    const { getByTestId } = render(<AddBar {...baseProps} />);
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    expect(getByTestId('liste-add-bar-dropdown')).toBeTruthy();
    expect(getByTestId(`liste-add-bar-hit-${HIT_A.ean}`)).toBeTruthy();
    expect(getByTestId(`liste-add-bar-hit-${HIT_B.ean}`)).toBeTruthy();
  });

  it('renders the « Suggestions » eyebrow above the default hits', () => {
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [HIT_A] },
      isFetching: false,
    });
    const { getByTestId } = render(<AddBar {...baseProps} />);
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    const eyebrow = getByTestId('liste-add-bar-suggestions-eyebrow');
    expect(eyebrow.props.children).toBe('Suggestions');
  });

  it('does NOT render the eyebrow once the user has typed a query', () => {
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [HIT_A] },
      isFetching: false,
    });
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT_A] },
      isFetching: false,
    });
    const { getByTestId, queryByTestId } = render(<AddBar {...baseProps} />);
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    fireEvent.changeText(input, 'ai');
    expect(queryByTestId('liste-add-bar-suggestions-eyebrow')).toBeNull();
  });

  it('does NOT render the empty-state row when the query is empty (default mode)', () => {
    // Default mode with zero default hits = silent empty dropdown.
    // « Aucun produit trouvé » is reserved for a *typed* query that
    // returned no results.
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
    const { getByTestId, queryByTestId } = render(<AddBar {...baseProps} />);
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    expect(queryByTestId('liste-add-bar-empty')).toBeNull();
  });

  it('calls the default-suggestions hook with limit=5', () => {
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [HIT_A] },
      isFetching: false,
    });
    render(<AddBar {...baseProps} />);
    const calls = mockUseDefaultSuggestions.mock.calls;
    expect(calls.length).toBeGreaterThan(0);
    expect(calls[calls.length - 1][0]).toMatchObject({ limit: 5 });
  });

  it('disables the search hook while the query is empty (so we never hit /product/search?q=)', () => {
    // Wave-13 contract — when q is empty, ``useProductSearch`` MUST
    // be called with ``enabled: false`` so the legacy wave-12 backend
    // q="" branch is never hit ; only the dedicated suggestions
    // endpoint runs. Phase-3 cleanup will remove that backend branch.
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [HIT_A] },
      isFetching: false,
    });
    render(<AddBar {...baseProps} />);
    const lastCall =
      mockUseProductSearch.mock.calls[
        mockUseProductSearch.mock.calls.length - 1
      ];
    expect(lastCall[0]).toBe('');
    expect(lastCall[1]).toMatchObject({ enabled: false });
  });

  it('renders typed search hits when the query is non-empty (search wins over suggestions)', () => {
    const TYPED = {
      ean: '9999999999999',
      name: 'TypedResult',
      brands: null,
      quantity: null,
      categories_tags: null,
      labels_tags: null,
      origins_tags: null,
      source: 'off',
    };
    // Both hooks are always mounted ; AddBar must pick the search
    // results when the user has typed something.
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [HIT_A] },
      isFetching: false,
    });
    mockUseProductSearch.mockReturnValue({
      data: { items: [TYPED] },
      isFetching: false,
    });
    const { getByTestId, queryByTestId } = render(<AddBar {...baseProps} />);
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    fireEvent.changeText(input, 'ty');
    expect(getByTestId(`liste-add-bar-hit-${TYPED.ean}`)).toBeTruthy();
    // Default suggestion HIT_A must NOT leak into the dropdown when a
    // search query is active.
    expect(queryByTestId(`liste-add-bar-hit-${HIT_A.ean}`)).toBeNull();
  });

  it('reverts to suggestions when the query is cleared after typing', () => {
    // Same component instance, both hooks always-mounted. The JSX
    // switch must show suggestions, not stale search results, after
    // the user clears the field with a typed query in flight.
    const TYPED = {
      ean: '9999999999999',
      name: 'TypedResult',
      brands: null,
      quantity: null,
      categories_tags: null,
      labels_tags: null,
      origins_tags: null,
      source: 'off',
    };
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [HIT_A] },
      isFetching: false,
    });
    mockUseProductSearch.mockReturnValue({
      data: { items: [TYPED] },
      isFetching: false,
    });
    const { getByTestId, queryByTestId } = render(<AddBar {...baseProps} />);
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    fireEvent.changeText(input, 'ty');
    expect(getByTestId(`liste-add-bar-hit-${TYPED.ean}`)).toBeTruthy();
    fireEvent.changeText(input, '');
    expect(getByTestId(`liste-add-bar-hit-${HIT_A.ean}`)).toBeTruthy();
    expect(queryByTestId(`liste-add-bar-hit-${TYPED.ean}`)).toBeNull();
  });
});

// Wave 9 (PO ticket 2026-05-13) — enriched secondary line in dropdown.
// PO « j'ai une liste massive de plein de pomme de terre et aucun moyen
// de les identifier précisement ». Row now shows ``brand · quantity ·
// 🇫🇷 · 🌱`` so identical-named products are distinguishable at a
// glance.
describe('AddBar — wave-9 enriched dropdown row (PO « pomme de terre »)', () => {
  const baseProps = {
    onSubmit: jest.fn(),
    onPressSuggestions: jest.fn(),
    onPressTemplates: jest.fn(),
    onPressVoice: jest.fn(),
  };

  beforeEach(() => {
    baseProps.onSubmit.mockReset();
    baseProps.onPressSuggestions.mockReset();
    baseProps.onPressTemplates.mockReset();
    baseProps.onPressVoice.mockReset();
    mockUseProductSearch.mockReset();
    mockUseDefaultSuggestions.mockReset();
    mockUseDefaultSuggestions.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
  });

  function typeQuery(getByTestId: (id: string) => any, q = 'pomme') {
    const input = getByTestId('liste-add-bar-input');
    fireEvent(input, 'focus');
    fireEvent.changeText(input, q);
  }

  it('renders ONLY the name when brand / quantity / origins / labels are all null', () => {
    const HIT = {
      ean: '3017620421001',
      name: 'Pomme de terre',
      brands: null,
      quantity: null,
      categories_tags: null,
      labels_tags: null,
      origins_tags: null,
      source: 'off',
    };
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT] },
      isFetching: false,
    });
    const { getByTestId, queryByTestId } = render(<AddBar {...baseProps} />);
    typeQuery(getByTestId);
    expect(getByTestId(`liste-add-bar-hit-${HIT.ean}`)).toBeTruthy();
    // No secondary line should appear when all enrich fields are null.
    expect(
      queryByTestId(`liste-add-bar-hit-${HIT.ean}-secondary`),
    ).toBeNull();
  });

  it('renders « brand · quantity » when those two are present but no flag / bio', () => {
    const HIT = {
      ean: '3017620421003',
      name: 'Pomme de terre',
      brands: 'Lidl',
      quantity: '2 kg',
      categories_tags: null,
      labels_tags: null,
      origins_tags: ['en:belgium'],
      source: 'off',
    };
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT] },
      isFetching: false,
    });
    const { getByTestId } = render(<AddBar {...baseProps} />);
    typeQuery(getByTestId);
    const secondary = getByTestId(
      `liste-add-bar-hit-${HIT.ean}-secondary`,
    );
    expect(secondary.props.children).toBe('Lidl · 2 kg');
  });

  it('renders the 🇫🇷 flag when origins_tags contains en:france', () => {
    const HIT = {
      ean: '3017620421002',
      name: 'Pomme de terre',
      brands: 'Carrefour',
      quantity: '1 kg',
      categories_tags: null,
      labels_tags: null,
      origins_tags: ['en:france'],
      source: 'off',
    };
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT] },
      isFetching: false,
    });
    const { getByTestId } = render(<AddBar {...baseProps} />);
    typeQuery(getByTestId);
    const secondary = getByTestId(
      `liste-add-bar-hit-${HIT.ean}-secondary`,
    );
    expect(secondary.props.children).toBe('Carrefour · 1 kg · 🇫🇷');
  });

  it('renders the 🌱 leaf when labels_tags carries en:organic', () => {
    const HIT = {
      ean: '3017620421006',
      name: 'Pomme de terre bio',
      brands: 'Bio Village',
      quantity: '500 g',
      categories_tags: null,
      labels_tags: ['en:organic', 'fr:bio'],
      origins_tags: ['en:france'],
      source: 'off',
    };
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT] },
      isFetching: false,
    });
    const { getByTestId } = render(<AddBar {...baseProps} />);
    typeQuery(getByTestId);
    const secondary = getByTestId(
      `liste-add-bar-hit-${HIT.ean}-secondary`,
    );
    expect(secondary.props.children).toBe(
      'Bio Village · 500 g · 🇫🇷 · 🌱',
    );
  });

  it('exposes the secondary line in the accessibility label for screen readers', () => {
    const HIT = {
      ean: '3017620421002',
      name: 'Pomme de terre',
      brands: 'Carrefour',
      quantity: '1 kg',
      categories_tags: null,
      labels_tags: null,
      origins_tags: ['en:france'],
      source: 'off',
    };
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT] },
      isFetching: false,
    });
    const { getByTestId } = render(<AddBar {...baseProps} />);
    typeQuery(getByTestId);
    const row = getByTestId(`liste-add-bar-hit-${HIT.ean}`);
    expect(row.props.accessibilityLabel).toBe(
      'Pomme de terre — Carrefour · 1 kg · 🇫🇷',
    );
  });
});
