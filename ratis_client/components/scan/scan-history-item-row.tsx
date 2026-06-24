// ratis_client/components/scan/scan-history-item-row.tsx
//
// Row rendering a single scan item inside a receipt accordion OR a label-group
// accordion. Status × match_method → label/colour mapping is delegated to
// `utils/scan-status.ts` (`mapStatusToUx`) so v2 (`accepted/unmatched`) and
// v3 (`matched/unresolved`) rows share the same renderer during the
// transition window — see ARCH_scan_history.md § Mapping match_method →
// couleur bouton, brief Bloc 9.

import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';
import type { ConsensusState, ReceiptItem } from '@/hooks/use-receipt-items';
import type { LabelGroupItem } from '@/hooks/use-label-group-items';
import {
  mapStatusToUx,
  formatRejectedReason,
  type ScanUxColor,
} from '@/utils/scan-status';

/**
 * NRC bloc E — minimal visual mapping for the live consensus state badge.
 * V0 visual is intentionally minimal (refactor en cours côté Claude Design).
 * Each state pairs an i18n label with a glyph + foreground colour. The badge
 * complements the existing match_method colour pill — it does not replace it.
 */
const CONSENSUS_BADGE: Record<
  ConsensusState,
  { glyph: string; color: string; labelKey: string }
> = {
  verified: {
    glyph: '✓', // ✓
    color: '#10B981',
    labelKey: 'scan.consensus_state.verified',
  },
  unverified: {
    glyph: '⚠', // ⚠
    color: '#FB923C',
    labelKey: 'scan.consensus_state.unverified',
  },
  controverse: {
    glyph: '?',
    color: '#FB923C',
    labelKey: 'scan.consensus_state.controverse',
  },
  pending: {
    glyph: '⧖', // ⧖ (hourglass-shape, monochrome friendly)
    color: '#FACC15',
    labelKey: 'scan.consensus_state.pending',
  },
  unresolved: {
    glyph: '?',
    color: 'rgba(255,255,255,0.55)',
    labelKey: 'scan.consensus_state.unresolved',
  },
};

/**
 * Discriminated adapter so a LabelGroupItem (accepted-only, no `status` field)
 * can be rendered by the same row. Label group items are always treated as
 * `status='accepted'` — the backend filters out everything else.
 */
export type ScanHistoryItemInput =
  | { kind: 'receipt'; item: ReceiptItem }
  | { kind: 'label_group'; item: LabelGroupItem };

function toReceiptItem(input: ScanHistoryItemInput): ReceiptItem {
  if (input.kind === 'receipt') return input.item;
  const it = input.item;
  return {
    scan_id: it.scan_id,
    scanned_name: null,
    product_name: it.product_name,
    // Label-group endpoint does not yet expose display_name — fall back to
    // product_name so the renderer stays consistent.
    display_name: it.product_name,
    product_ean: it.product_ean,
    quantity: 1,
    price_cents: it.price_cents,
    status: 'accepted',
    match_method: it.match_method,
    // NRC bloc E — pass through so the consensus badge renders.
    consensus_state: it.consensus_state ?? null,
  };
}

interface Props {
  item: ReceiptItem | ScanHistoryItemInput;
  /** Called when the user taps the coloured barcode button. Not called when
   *  `status='pending'` — the button renders as a non-interactive clock. */
  onPressBarcode: (item: ReceiptItem) => void;
}

function normalizeItem(raw: Props['item']): ReceiptItem {
  if ('kind' in raw) return toReceiptItem(raw);
  return raw;
}

interface DisplayName {
  text: string;
  italic: boolean;
}

/** Resolve which name to show as the row's main line. The mapper drives
 *  colour and label, but the human-readable "Lait demi-écrémé 1L" choice
 *  stays here because it depends on which fields are populated.
 *
 *  Backend (PR feat/off-sync-multi-fields) now exposes ``display_name``
 *  composed by ``ratis_core.products.pick_display_name`` from the OFF
 *  multi-field columns. We prefer it over ``product_name`` (the raw OFF
 *  best-of) but keep ``product_name`` as a backward-compat fallback for
 *  older app versions hitting newer backends — and vice-versa. */
function deriveName(
  item: ReceiptItem,
  t: (k: string) => string,
): DisplayName {
  // Pending → italic placeholder ("Traitement en cours…").
  if (item.status === 'pending') {
    return { text: t('scan.history.item.subtitle_pending'), italic: true };
  }
  const bestName = item.display_name ?? item.product_name;
  // Matched / accepted → enriched name preferred, then scanned_name, fallback.
  if (item.status === 'matched' || item.status === 'accepted') {
    if (bestName) return { text: bestName, italic: false };
    if (item.scanned_name) return { text: item.scanned_name, italic: false };
    return { text: t('scan.history.item.article_to_identify'), italic: true };
  }
  // Unresolved / unmatched / rejected → scanned_name if any, else placeholder.
  if (item.scanned_name) return { text: item.scanned_name, italic: false };
  return { text: t('scan.history.item.article_to_identify'), italic: true };
}

function formatPrice(cents: number | null): string | null {
  if (cents == null) return null;
  return `${(cents / 100).toFixed(2).replace('.', ',')}€`;
}

const BUTTON_COLORS: Record<ScanUxColor, { bg: string; border: string; text: string }> = {
  green: { bg: 'rgba(16,185,129,0.18)', border: '#10B981', text: '#10B981' },
  // green-dimmer: admin-validated rows — visually softer than fresh matches.
  'green-dimmer': { bg: 'rgba(16,185,129,0.10)', border: 'rgba(16,185,129,0.55)', text: 'rgba(16,185,129,0.85)' },
  orange: { bg: 'rgba(251,146,60,0.20)', border: '#FB923C', text: '#FB923C' },
  red: { bg: 'rgba(239,68,68,0.20)', border: '#EF4444', text: '#EF4444' },
  gray: { bg: 'rgba(255,255,255,0.06)', border: 'rgba(255,255,255,0.15)', text: 'rgba(255,255,255,0.55)' },
};

const BUTTON_GLYPHS: Record<ScanUxColor, string> = {
  green: '🟢',
  'green-dimmer': '🟢',
  orange: '🟠',
  red: '🔴',
  gray: '⏰',
};

/** Translate the `formatRejectedReason` return value into a final user-facing
 *  string. The mapper returns either a bare i18n key or `"<key>|<score>"`
 *  for the scored fuzzy variants. */
function resolveReasonText(
  reasonKeyOrTuple: string,
  t: (k: string, v?: Record<string, unknown>) => string,
): string {
  const pipeIdx = reasonKeyOrTuple.indexOf('|');
  if (pipeIdx === -1) return t(reasonKeyOrTuple);
  const key = reasonKeyOrTuple.slice(0, pipeIdx);
  const score = reasonKeyOrTuple.slice(pipeIdx + 1);
  return t(key, { score });
}

export function ScanHistoryItemRow({ item: raw, onPressBarcode }: Props) {
  const { t } = useTranslation();
  const item = normalizeItem(raw);
  // Philosophie « vert = consensus only » (2026-05-01) — la grosse pastille
  // verte n'est accordée qu'aux actes d'autorité explicite (barcode user /
  // manual_admin) ou aux matches auto validés par le consensus crowdsourcé
  // (`consensus_state='verified'`). Le `consensus_state` est forwardé ici
  // pour piloter cette règle. Voir `utils/scan-status.ts` § matchedUx.
  const ux = mapStatusToUx(
    item.status,
    item.match_method,
    item.consensus_state ?? null,
  );
  const name = deriveName(item, t);
  const price = formatPrice(item.price_cents);
  const colors = BUTTON_COLORS[ux.color];
  const disabled = ux.color === 'gray';

  // Subtitle: rejected_reason translated when the mapper says hasReason,
  // otherwise the legacy OCR-brut hint when product_name and scanned_name
  // diverge for a barcode-matched row (debug aid for the user).
  let subtitle: string | null = null;
  if (ux.hasReason) {
    subtitle = resolveReasonText(formatRejectedReason(item.rejected_reason ?? null), t);
  } else if (
    (item.status === 'matched' || item.status === 'accepted') &&
    (item.match_method === 'barcode' || item.match_method === 'barcode_ean' ||
     item.match_method === 'manual_admin' || item.match_method === 'manual') &&
    item.scanned_name &&
    // Compare against the resolved display name (display_name with product_name
    // fallback) so the OCR-brut hint stays meaningful when display_name diverges
    // from product_name (e.g. "Yaourt à boire fraise" vs raw "Hipro +").
    item.scanned_name !== (item.display_name ?? item.product_name)
  ) {
    subtitle = t('scan.history.item.subtitle_ocr_brut', { text: item.scanned_name });
  }

  // NRC bloc E — consensus badge. Renders only when the backend exposes a
  // concrete state (older backends omit the field, ``null`` ⇔ no badge).
  const consensusBadge =
    item.consensus_state ? CONSENSUS_BADGE[item.consensus_state] : null;

  return (
    <View style={styles.row} testID={`scan-history-item-row-${item.scan_id}`}>
      <View style={styles.textCol}>
        <View style={styles.nameRow}>
          <Text
            style={[styles.name, name.italic && styles.nameItalic]}
            numberOfLines={1}
          >
            {name.text}
          </Text>
          {consensusBadge && (
            <Text
              style={[styles.consensusBadge, { color: consensusBadge.color }]}
              testID={`scan-history-item-consensus-${item.scan_id}`}
              accessibilityLabel={t(consensusBadge.labelKey)}
            >
              {consensusBadge.glyph}
            </Text>
          )}
        </View>
        {subtitle && (
          <Text
            style={styles.subtitle}
            numberOfLines={2}
            testID={`scan-history-item-subtitle-${item.scan_id}`}
          >
            {subtitle}
          </Text>
        )}
      </View>
      {item.quantity != null && item.quantity > 1 && (
        <Text style={styles.qty}>{t('scan.history.item.quantity_suffix', { n: item.quantity })}</Text>
      )}
      {price && <Text style={styles.price}>{price}</Text>}
      <Pressable
        onPress={() => !disabled && onPressBarcode(item)}
        disabled={disabled}
        testID={`scan-history-item-barcode-${item.scan_id}`}
        accessibilityRole="button"
        accessibilityLabel={t(ux.labelKey)}
        accessibilityState={{ disabled }}
        style={[styles.barcodeBtn, { backgroundColor: colors.bg, borderColor: colors.border }]}
      >
        <Text style={[styles.barcodeGlyph, { color: colors.text }]}>
          {BUTTON_GLYPHS[ux.color]}
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.04)',
  },
  textCol: { flex: 1, minWidth: 0 },
  // NRC bloc E — name + consensus badge sit on the same line so the badge
  // stays visually coupled to the product label even when the row truncates.
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  name: { fontSize: 13, color: 'rgba(255,255,255,0.9)', fontWeight: '600', flexShrink: 1 },
  nameItalic: { fontStyle: 'italic', color: 'rgba(255,255,255,0.6)' },
  consensusBadge: { fontSize: 12, fontWeight: '700' },
  subtitle: { fontSize: 11, color: 'rgba(255,255,255,0.5)', marginTop: 2, fontStyle: 'italic' },
  qty: { fontSize: 11, fontWeight: '700', color: 'rgba(255,255,255,0.55)' },
  price: { fontSize: 12, fontWeight: '700', color: '#FFB800' },
  barcodeBtn: {
    width: 36,
    height: 36,
    borderRadius: 10,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  barcodeGlyph: { fontSize: 14 },
});
