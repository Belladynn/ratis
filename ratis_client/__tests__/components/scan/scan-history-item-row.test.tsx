// Tests for ScanHistoryItemRow.
//
// Updated 2026-05-01 (Bloc 9) — pipeline-v3 status/match_method semantics.
// The previous v2-only assertions (e.g. "fuzzy → orange", "unmatched → red")
// are kept as backward-compat coverage but updated to reflect the unified
// mapping in `utils/scan-status.ts`. The brief explicitly aliases v2 values
// to v3 UX, so these tests now assert the v3 behaviour with v2 input shapes.
//
// Updated 2026-05-01 PM — « vert = consensus only ». La grosse pastille
// devient verte uniquement pour les actes d'autorité explicite (barcode,
// manual_admin) OU pour les matches auto avec `consensus_state='verified'`.
// Les matches auto sans consensus validé restent orange (safe default). Les
// tests de fuzzy legacy (v2) sans consensus_state sont maj en conséquence.

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { ScanHistoryItemRow } from '@/components/scan/scan-history-item-row';
import type { ReceiptItem } from '@/hooks/use-receipt-items';

function make(partial: Partial<ReceiptItem>): ReceiptItem {
  return {
    scan_id: 'scan-1',
    scanned_name: null,
    product_name: null,
    product_ean: null,
    quantity: 1,
    price_cents: 100,
    status: 'accepted',
    match_method: null,
    ...partial,
  };
}

describe('ScanHistoryItemRow — legacy v2 inputs', () => {
  it('renders green barcode button for accepted + barcode_ean (v2 → v3 matched_barcode)', () => {
    const item = make({
      status: 'accepted',
      match_method: 'barcode_ean',
      product_name: 'Lait demi-écrémé 1L',
      scanned_name: 'LAIT DE DE-ECR',
      price_cents: 129,
    });
    const { getByTestId, getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('Lait demi-écrémé 1L')).toBeTruthy();
    expect(getByText('1,29€')).toBeTruthy();
    const btn = getByTestId('scan-history-item-barcode-scan-1');
    expect(btn.props.accessibilityState?.disabled).toBeFalsy();
    // Brief Bloc 9: fuzzy/barcode/etc. all map to green; only unresolved is orange.
    // Visual hint: the barcode button glyph is the green dot.
    expect(getByText('🟢')).toBeTruthy();
  });

  it('v2 fuzzy without verified consensus → orange (vert = consensus only)', () => {
    // Rule update 2026-05-01: a non-authority match (fuzzy/observed_name/
    // knowledge/...) only earns the green dot when the live consensus is
    // `verified`. Legacy v2 fuzzy rows have no consensus row → orange.
    // Pre-2026-05-01 this rendered green; we now reflect the new rule.
    const item = make({
      status: 'accepted',
      match_method: 'fuzzy',
      product_name: 'Nutella 400g',
      price_cents: 489,
      consensus_state: null,
    });
    const { getByText, queryByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('Nutella 400g')).toBeTruthy();
    expect(queryByText('🟢')).toBeNull();
    expect(getByText('🟠')).toBeTruthy();
  });

  it('v2 fuzzy WITH verified consensus → green (community-validated)', () => {
    const item = make({
      status: 'accepted',
      match_method: 'fuzzy',
      product_name: 'Nutella 400g',
      price_cents: 489,
      consensus_state: 'verified',
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟢')).toBeTruthy();
  });

  it('renders orange button for unmatched with scanned_name (v2 → v3 unresolved)', () => {
    const item = make({
      status: 'unmatched',
      match_method: null,
      scanned_name: 'PATE A TART FERRE',
      product_name: null,
      price_cents: 299,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('PATE A TART FERRE')).toBeTruthy();
    // Old "Non reconnu — scanne le code-barre" replaced by translated reason.
    // Without rejected_reason set (legacy v2 row), default → "Non identifié".
    expect(getByText('Non identifié')).toBeTruthy();
    expect(getByText('🟠')).toBeTruthy();
  });

  it('renders "Article à identifier" italic when unmatched + scanned_name is null', () => {
    const item = make({
      status: 'unmatched',
      match_method: null,
      scanned_name: null,
      product_name: null,
      price_cents: 150,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('Article à identifier')).toBeTruthy();
    // Default fallback when no rejected_reason — "Non identifié".
    expect(getByText('Non identifié')).toBeTruthy();
  });

  it('renders a non-tappable clock indicator for pending items', () => {
    const item = make({
      status: 'pending',
      match_method: null,
      scanned_name: null,
      product_name: null,
      price_cents: null,
    });
    const onPress = jest.fn();
    const { getByTestId, getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={onPress} />,
    );
    const btn = getByTestId('scan-history-item-barcode-scan-1');
    fireEvent.press(btn);
    expect(onPress).not.toHaveBeenCalled();
    expect(getByText(/Traitement en cours/)).toBeTruthy();
    expect(getByText('⏰')).toBeTruthy();
  });

  it('invokes onPressBarcode with the item when the barcode button is pressed', () => {
    const item = make({
      status: 'unmatched',
      match_method: null,
      scanned_name: 'COLA 33CL',
      price_cents: 99,
    });
    const onPress = jest.fn();
    const { getByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={onPress} />,
    );
    fireEvent.press(getByTestId('scan-history-item-barcode-scan-1'));
    expect(onPress).toHaveBeenCalledWith(item);
  });

  it('shows quantity suffix when quantity > 1', () => {
    const item = make({
      status: 'accepted',
      match_method: 'barcode_ean',
      product_name: 'Lait',
      quantity: 3,
      price_cents: 387,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('×3')).toBeTruthy();
  });

  it('shows OCR subtitle when v2 accepted+barcode_ean and scanned_name differs', () => {
    const item = make({
      status: 'accepted',
      match_method: 'barcode_ean',
      product_name: 'Lait demi-écrémé 1L',
      scanned_name: 'LAIT DE DE-ECR',
      price_cents: 129,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText(/OCR: LAIT DE DE-ECR/)).toBeTruthy();
  });
});

describe('ScanHistoryItemRow — pipeline-v3 inputs', () => {
  it('matched + barcode renders green and surfaces product_name', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Coca-Cola 33cl',
      product_ean: '5000000000019',
      price_cents: 199,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('Coca-Cola 33cl')).toBeTruthy();
    expect(getByText('🟢')).toBeTruthy();
  });

  it('matched + manual_admin renders dimmer green (admin-validated)', () => {
    const item = make({
      status: 'matched',
      match_method: 'manual_admin',
      product_name: 'Pâtes Barilla 500g',
      price_cents: 169,
    });
    const { getByTestId, getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('Pâtes Barilla 500g')).toBeTruthy();
    const btn = getByTestId('scan-history-item-barcode-scan-1');
    // green-dimmer keeps the same emoji but uses softer rgba — assert via the
    // border colour on the button style rather than the emoji.
    const flat = (Array.isArray(btn.props.style)
      ? Object.assign({}, ...btn.props.style)
      : btn.props.style) as { borderColor?: string };
    expect(flat.borderColor).toBe('rgba(16,185,129,0.55)');
  });

  it('unresolved renders orange + translates rejected_reason as subtitle', () => {
    const item = make({
      status: 'unresolved',
      match_method: null,
      scanned_name: 'YGRT NAT 4X125G',
      rejected_reason: 'no_fuzzy_candidate',
      price_cents: 235,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('YGRT NAT 4X125G')).toBeTruthy();
    expect(getByText('Aucun produit similaire trouvé')).toBeTruthy();
    expect(getByText('🟠')).toBeTruthy();
  });

  it('unresolved with scored fuzzy_below_threshold surfaces the score in the subtitle', () => {
    const item = make({
      status: 'unresolved',
      match_method: null,
      scanned_name: 'PAIN BLANC',
      rejected_reason: 'fuzzy_below_threshold_0.654',
      price_cents: 120,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('Correspondance trop faible (0.65)')).toBeTruthy();
  });

  it('rejected renders red and surfaces the rejected_reason', () => {
    const item = make({
      status: 'rejected',
      match_method: null,
      scanned_name: 'GARBAGE TEXT',
      rejected_reason: 'ocr_garbage',
      price_cents: null,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('GARBAGE TEXT')).toBeTruthy();
    expect(getByText('Texte illisible sur le ticket')).toBeTruthy();
    expect(getByText('🔴')).toBeTruthy();
  });

  it('matched + knowledge renders green without subtitle when names match', () => {
    const item = make({
      status: 'matched',
      match_method: 'knowledge',
      product_name: 'Yaourt nature',
      scanned_name: 'Yaourt nature',
      price_cents: 99,
    });
    const { queryByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    // No subtitle expected — names match and reason is irrelevant for matched.
    expect(queryByTestId('scan-history-item-subtitle-scan-1')).toBeNull();
  });

  it('prefers display_name over product_name for the row label (PR multi-fields)', () => {
    // Backend now exposes display_name composed by pick_display_name from the
    // OFF multi-field columns. The row should display it instead of the raw
    // OFF best-of (product_name).
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Hipro +',
      display_name: 'Hipro + protéines fraise',
      scanned_name: 'HIPRO+',
      price_cents: 389,
    });
    const { getByText, queryByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('Hipro + protéines fraise')).toBeTruthy();
    expect(queryByText('Hipro +')).toBeNull();
  });

  it('falls back to product_name when display_name is absent (older backend)', () => {
    // Backward-compat path : a backend that does not yet emit display_name
    // (rolling deploy, or a legacy v2 row) must still render product_name.
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Coca-Cola 33cl',
      // display_name intentionally omitted
      price_cents: 199,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('Coca-Cola 33cl')).toBeTruthy();
  });

  it('OCR-brut subtitle compares scanned_name against display_name (not product_name)', () => {
    // When display_name diverges from product_name, the OCR-brut hint must
    // be driven by display_name to stay meaningful for the user.
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Hipro +',
      display_name: 'Hipro + protéines fraise',
      scanned_name: 'Hipro + protéines fraise',  // identical to display_name
      price_cents: 389,
    });
    const { queryByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    // No subtitle expected — scanned_name matches display_name.
    expect(queryByTestId('scan-history-item-subtitle-scan-1')).toBeNull();
  });

  it('exposes the i18n status label via accessibilityLabel for screen readers', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Nutella',
      price_cents: 489,
    });
    const { getByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    const btn = getByTestId('scan-history-item-barcode-scan-1');
    expect(btn.props.accessibilityLabel).toBe('Reconnu (code-barre)');
  });
});

// ============================================================
// NRC bloc E — consensus_state badge
// ============================================================

describe('ScanHistoryItemRow — consensus_state badge (NRC bloc E)', () => {
  /**
   * Helper : pick the actual style object out of a node's `style` prop, which
   * RN passes either as a flat object or an array of objects.
   */
  function flatStyle(node: { props: { style?: unknown } }) {
    const s = node.props.style;
    if (Array.isArray(s)) return Object.assign({}, ...s) as Record<string, unknown>;
    return (s ?? {}) as Record<string, unknown>;
  }

  it('renders no consensus badge when the field is null', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Lait 1L',
      consensus_state: null,
    });
    const { queryByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(queryByTestId('scan-history-item-consensus-scan-1')).toBeNull();
  });

  it('renders no consensus badge when the field is absent (older backend)', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Lait 1L',
      // consensus_state intentionally omitted
    });
    const { queryByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(queryByTestId('scan-history-item-consensus-scan-1')).toBeNull();
  });

  it('renders a green ✓ badge for verified', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Lait 1L',
      consensus_state: 'verified',
    });
    const { getByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    const badge = getByTestId('scan-history-item-consensus-scan-1');
    expect(badge.props.children).toBe('✓');
    expect(flatStyle(badge).color).toBe('#10B981');
    expect(badge.props.accessibilityLabel).toBe('Confirmé');
  });

  it('renders an orange ⚠ badge for unverified', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Lait 1L',
      consensus_state: 'unverified',
    });
    const { getByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    const badge = getByTestId('scan-history-item-consensus-scan-1');
    expect(badge.props.children).toBe('⚠');
    expect(flatStyle(badge).color).toBe('#FB923C');
    expect(badge.props.accessibilityLabel).toBe('Contesté');
  });

  it('renders an orange ? badge for controverse', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Lait 1L',
      consensus_state: 'controverse',
    });
    const { getByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    const badge = getByTestId('scan-history-item-consensus-scan-1');
    expect(badge.props.children).toBe('?');
    expect(flatStyle(badge).color).toBe('#FB923C');
    expect(badge.props.accessibilityLabel).toBe('En débat');
  });

  it('renders a yellow ⧖ badge for pending', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Lait 1L',
      consensus_state: 'pending',
    });
    const { getByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    const badge = getByTestId('scan-history-item-consensus-scan-1');
    expect(badge.props.children).toBe('⧖');
    expect(flatStyle(badge).color).toBe('#FACC15');
    expect(badge.props.accessibilityLabel).toBe('En attente');
  });

  it('renders a grey ? badge for unresolved', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Lait 1L',
      consensus_state: 'unresolved',
    });
    const { getByTestId } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    const badge = getByTestId('scan-history-item-consensus-scan-1');
    expect(badge.props.children).toBe('?');
    expect(flatStyle(badge).color).toBe('rgba(255,255,255,0.55)');
    expect(badge.props.accessibilityLabel).toBe('Non résolu');
  });

  it('passes consensus_state through the LabelGroupItem adapter', () => {
    // Rendering with a `kind: 'label_group'` input must still surface the
    // consensus badge — the adapter `toReceiptItem` is responsible for
    // forwarding the field.
    const { getByTestId } = render(
      <ScanHistoryItemRow
        item={{
          kind: 'label_group',
          item: {
            scan_id: 'scan-lg',
            product_name: 'Yaourt',
            product_ean: '123',
            price_cents: 99,
            match_method: 'barcode_ean',
            scanned_at: null,
            consensus_state: 'verified',
          },
        }}
        onPressBarcode={jest.fn()}
      />,
    );
    const badge = getByTestId('scan-history-item-consensus-scan-lg');
    expect(badge.props.children).toBe('✓');
  });
});

// ============================================================
// 2026-05-01 — « vert = consensus only » regression suite
// ============================================================
//
// La grosse pastille (BUTTON_COLORS) ne devient verte que :
//   - pour les actes d'autorité explicite (barcode, manual_admin), QUEL QUE
//     SOIT le `consensus_state`
//   - pour les matches auto (fuzzy_pending, observed_name, fuzzy_strict
//     legacy, ...) UNIQUEMENT quand `consensus_state='verified'`
// Tous les autres cas (auto sans verified) sont **orange** (safe default).
//
// Cette suite exerce chaque combinaison clé ; le petit badge consensus_state
// (Bloc E) reste indépendant et n'est pas testé ici.

describe("ScanHistoryItemRow — grosse pastille « vert = consensus only »", () => {
  it('barcode + null consensus → vert (autorité user)', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Coca 33cl',
      price_cents: 199,
      consensus_state: null,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟢')).toBeTruthy();
  });

  it('barcode + unverified consensus → vert (autorité user — bypass consensus)', () => {
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Coca 33cl',
      price_cents: 199,
      consensus_state: 'unverified',
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟢')).toBeTruthy();
  });

  it('barcode + controverse consensus → vert (autorité user — bypass consensus)', () => {
    // Cas extrême : même quand la communauté débat, un user ayant scanné
    // un code-barre conserve sa pastille verte.
    const item = make({
      status: 'matched',
      match_method: 'barcode',
      product_name: 'Coca 33cl',
      price_cents: 199,
      consensus_state: 'controverse',
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟢')).toBeTruthy();
  });

  it('manual_admin + null consensus → vert dimmer (autorité admin)', () => {
    const item = make({
      status: 'matched',
      match_method: 'manual_admin',
      product_name: 'Pâtes 500g',
      price_cents: 169,
      consensus_state: null,
    });
    const { getByTestId, getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    // green-dimmer keeps the green emoji but uses softer rgba border.
    expect(getByText('🟢')).toBeTruthy();
    const btn = getByTestId('scan-history-item-barcode-scan-1');
    const flat = (Array.isArray(btn.props.style)
      ? Object.assign({}, ...btn.props.style)
      : btn.props.style) as { borderColor?: string };
    expect(flat.borderColor).toBe('rgba(16,185,129,0.55)');
  });

  it('fuzzy_pending + verified → vert (validé crowdsourcé)', () => {
    const item = make({
      status: 'matched',
      match_method: 'fuzzy_pending',
      product_name: 'Yaourt nature',
      price_cents: 99,
      consensus_state: 'verified',
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟢')).toBeTruthy();
  });

  it('fuzzy_pending + null consensus → orange (régression : était vert avant)', () => {
    // Bug ciblé par cette PR : avant 2026-05-01, fuzzy_pending sans
    // consensus rendait green à tort. Doit désormais être orange.
    const item = make({
      status: 'matched',
      match_method: 'fuzzy_pending',
      product_name: 'Yaourt nature',
      price_cents: 99,
      consensus_state: null,
    });
    const { getByText, queryByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(queryByText('🟢')).toBeNull();
    expect(getByText('🟠')).toBeTruthy();
  });

  it('fuzzy_pending + unverified consensus → orange', () => {
    const item = make({
      status: 'matched',
      match_method: 'fuzzy_pending',
      product_name: 'Yaourt nature',
      price_cents: 99,
      consensus_state: 'unverified',
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟠')).toBeTruthy();
  });

  it('fuzzy_pending + controverse consensus → orange', () => {
    const item = make({
      status: 'matched',
      match_method: 'fuzzy_pending',
      product_name: 'Yaourt nature',
      price_cents: 99,
      consensus_state: 'controverse',
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟠')).toBeTruthy();
  });

  it('fuzzy_pending + pending consensus → orange (safe default)', () => {
    const item = make({
      status: 'matched',
      match_method: 'fuzzy_pending',
      product_name: 'Yaourt nature',
      price_cents: 99,
      consensus_state: 'pending',
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟠')).toBeTruthy();
  });

  it('observed_name + verified → vert', () => {
    const item = make({
      status: 'matched',
      match_method: 'observed_name',
      product_name: 'Pain blanc',
      price_cents: 120,
      consensus_state: 'verified',
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟢')).toBeTruthy();
  });

  it('observed_name + null consensus → orange', () => {
    const item = make({
      status: 'matched',
      match_method: 'observed_name',
      product_name: 'Pain blanc',
      price_cents: 120,
      consensus_state: null,
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟠')).toBeTruthy();
  });

  it('fuzzy_strict legacy + null consensus → orange (vieux scans pré-NRC)', () => {
    // Les rangs persistés avant le rollout NRC (2026-04-30+) ont
    // match_method='fuzzy_strict' et pas de ligne consensus_state.
    // Ils doivent maintenant être orange — pas vert à tort.
    const item = make({
      status: 'matched',
      match_method: 'fuzzy_strict',
      product_name: 'Lait 1L',
      price_cents: 109,
      consensus_state: null,
    });
    const { getByText, queryByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(queryByText('🟢')).toBeNull();
    expect(getByText('🟠')).toBeTruthy();
  });

  it('status=rejected → red (préservé, indépendant du consensus)', () => {
    const item = make({
      status: 'rejected',
      match_method: null,
      scanned_name: 'GARBAGE',
      rejected_reason: 'ocr_garbage',
      consensus_state: 'verified',  // sanity check : ignored for rejected
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🔴')).toBeTruthy();
  });

  it('status=pending → gray ⏰ (préservé, indépendant du consensus)', () => {
    const item = make({
      status: 'pending',
      match_method: null,
      consensus_state: 'verified',  // sanity check : ignored for pending
    });
    const { getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('⏰')).toBeTruthy();
  });

  it('le petit badge consensus_state (Bloc E) reste affiché en plus', () => {
    // Sanity : le mapping de la grosse pastille ne supprime pas le petit
    // badge consensus additif. Les deux signaux doivent coexister.
    const item = make({
      status: 'matched',
      match_method: 'fuzzy_pending',
      product_name: 'Yaourt nature',
      price_cents: 99,
      consensus_state: 'unverified',
    });
    const { getByTestId, getByText } = render(
      <ScanHistoryItemRow item={item} onPressBarcode={jest.fn()} />,
    );
    expect(getByText('🟠')).toBeTruthy();
    const badge = getByTestId('scan-history-item-consensus-scan-1');
    expect(badge.props.children).toBe('⚠');
  });
});
