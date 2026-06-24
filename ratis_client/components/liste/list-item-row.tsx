/**
 * Liste — ItemRow (V5 strict iso).
 *
 * Reference visual : `Ratis_handoff/screenshots/V5-FINAL-iso/Liste Courses.png`.
 * Reference JSX    : `Ratis_handoff/lib/ratis-liste-ui.jsx` lines 41-143
 *                    (`ItemRowLU`).
 *
 * Layout
 * ------
 *   [☐]  [icon tile]  Brand UPPERCASE        [- 2 +]   1,49€
 *                     Lait demi-écrémé 1L
 *
 *  - Custom checkbox (24×24) — terracotta gradient when checked.
 *  - Category icon tile colored per category (left vertical accent strip).
 *  - Body : optional `brand` (uppercase eyebrow) + product name.
 *  - Quantity Stepper (design-system primitive).
 *  - Price right (gold) — optional `unitPrice` × quantity, displayed only
 *    when caller supplies a price. Strikethrough + opacity when checked.
 *  - CheckBurst animation triggered on transition `unchecked → checked`.
 *
 * V5 strict iso : the V4 trash icon was removed (cf `Liste Courses.png`).
 * `onDelete` is kept in the prop interface so the contract holds for
 * programmatic deletion (e.g. V2 swipe-to-delete) without a UI affordance.
 *
 * Block layout (handoff iso) : rows do NOT carry their own border-radius
 * nor a margin-bottom — they are stacked flush inside a parent
 * `items-block` container that owns the rounded corners and clips overflow.
 * Internal separation is a 1px hairline divider painted on each row's
 * bottom edge, except the LAST row (`isLast`) which sits on the container's
 * bottom edge. Mirrors `ItemRowLU` `last` prop in
 * `Ratis_handoff/lib/ratis-liste-ui.jsx` lines 41-143.
 *
 * Token derogation : numeric values come straight from the JSX iso source —
 * see `chunk-3-followups.md` § 10 for the rationale.
 */

import React, { useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import * as Haptics from 'expo-haptics';

import { CheckBurst, Stepper } from '@/components/design-system';
import { Colors } from '@/constants/theme';
import type { ShoppingListItem } from '@/types/shopping-list';

/**
 * V5 list categories — palette mapped from
 * `Ratis_handoff/lib/ratis-liste-data.jsx`. Falls back to "other" if the
 * item carries no recognised category.
 */
export type CategoryKey =
  | 'produce'
  | 'dairy'
  | 'meat'
  | 'bakery'
  | 'pantry'
  | 'frozen'
  | 'drinks'
  | 'snacks'
  | 'hygiene'
  | 'other';

type CategoryDef = {
  icon: string;
  color: string;
  rgb: string; // "r,g,b" — used for rgba() alpha tinting
};

const CATEGORIES: Record<CategoryKey, CategoryDef> = {
  produce: { icon: '🥬', color: '#5EE5C2', rgb: '94,229,194' },
  dairy: { icon: '🥛', color: '#A78BFA', rgb: '167,139,250' },
  meat: { icon: '🍖', color: '#FB7185', rgb: '251,113,133' },
  bakery: { icon: '🥖', color: '#FFB800', rgb: '255,184,0' },
  pantry: { icon: '🍝', color: '#F59E0B', rgb: '245,158,11' },
  frozen: { icon: '🧊', color: '#67E8F9', rgb: '103,232,249' },
  drinks: { icon: '🥤', color: '#FF6B35', rgb: '255,107,53' },
  snacks: { icon: '🍪', color: '#FCA5A5', rgb: '252,165,165' },
  hygiene: { icon: '🧴', color: '#C4B5FD', rgb: '196,181,252' },
  other: { icon: '🛒', color: 'rgba(255,255,255,0.5)', rgb: '255,255,255' },
};

export type ListItemRowExtras = {
  brand?: string | null;
  category?: CategoryKey | null;
  /** Estimated unit price in major currency unit (e.g. 1.49 for 1,49€). */
  unitPrice?: number | null;
  /** When provided, replaces the default qty stepper handler. */
  onQuantityChange?: (next: number) => void;
  /**
   * Position flags within the items block container. Used to suppress the
   * bottom hairline divider on the last row (matching the handoff iso
   * where rows are merged into a single rounded block; cf
   * `Ratis_handoff/lib/ratis-liste-ui.jsx` `ItemRowLU` `last` prop).
   * `isFirst` is currently informational (the container handles the top
   * rounded corner) but kept symmetric for future use.
   */
  isFirst?: boolean;
  isLast?: boolean;
};

export interface ListItemRowProps extends ListItemRowExtras {
  item: ShoppingListItem;
  onToggle: () => void;
  onDelete: () => void;
}

function fmtPrice(amount: number): string {
  return amount.toFixed(2).replace('.', ',') + '€';
}

export function ListItemRow({
  item,
  onToggle,
  // V5 : trash icon removed; `onDelete` prop kept for programmatic delete
  // (V2 swipe pattern) but no UI affordance ships in V1.
  onDelete: _onDelete,
  brand,
  category,
  unitPrice,
  onQuantityChange,
  isFirst: _isFirst = false,
  isLast = false,
}: ListItemRowProps) {
  const cat =
    CATEGORIES[(category ?? 'other') as CategoryKey] ?? CATEGORIES.other;
  const [burst, setBurst] = useState(false);
  const wasChecked = useRef(item.checked);

  useEffect(() => {
    if (item.checked && !wasChecked.current) {
      setBurst(true);
      const id = setTimeout(() => setBurst(false), 700);
      wasChecked.current = item.checked;
      return () => clearTimeout(id);
    }
    wasChecked.current = item.checked;
    return undefined;
  }, [item.checked]);

  const handleToggle = () => {
    Haptics.selectionAsync().catch(() => undefined);
    onToggle();
  };

  const totalPrice =
    typeof unitPrice === 'number' ? unitPrice * item.quantity : null;

  return (
    <View
      testID="list-item-row"
      style={[
        styles.row,
        {
          backgroundColor: item.checked
            ? `rgba(${cat.rgb},0.06)`
            : `rgba(${cat.rgb},0.14)`,
          borderLeftColor: `rgba(${cat.rgb},${item.checked ? 0.2 : 0.7})`,
        },
        // Iso handoff: rows merge into a single block; each row paints a
        // bottom hairline divider EXCEPT the last (cf `ItemRowLU` `last`
        // prop in `Ratis_handoff/lib/ratis-liste-ui.jsx`).
        !isLast && styles.rowDivider,
      ]}
    >
      <View>
        <Pressable
          testID="list-item-row-toggle"
          onPress={handleToggle}
          hitSlop={6}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: item.checked }}
          accessibilityLabel={item.product_name}
          style={styles.checkboxHit}
        >
          {item.checked ? (
            <LinearGradient
              colors={[cat.color, `rgba(${cat.rgb},0.6)`]}
              start={{ x: 0, y: 0 }}
              end={{ x: 0, y: 1 }}
              style={[
                styles.checkbox,
                { borderColor: `rgba(${cat.rgb},0.5)` },
              ]}
            >
              <Text style={styles.tick}>✓</Text>
            </LinearGradient>
          ) : (
            <View style={[styles.checkbox, styles.checkboxEmpty]} />
          )}
        </Pressable>
        <CheckBurst
          play={burst}
          color={cat.color}
          originX={12}
          originY={12}
          testID="list-item-row-burst"
        />
      </View>

      <View
        style={[
          styles.iconTile,
          {
            backgroundColor: `rgba(${cat.rgb},0.22)`,
            borderColor: `rgba(${cat.rgb},0.45)`,
            opacity: item.checked ? 0.4 : 1,
          },
        ]}
      >
        <Text style={styles.iconTxt}>{cat.icon}</Text>
      </View>

      <View style={styles.body}>
        {brand ? (
          <Text style={styles.brand} numberOfLines={1}>
            {brand}
          </Text>
        ) : null}
        <Text
          style={[styles.name, item.checked && styles.nameDone]}
          numberOfLines={1}
        >
          {item.product_name}
        </Text>
      </View>

      <Stepper
        testID="list-item-row-stepper"
        value={item.quantity}
        min={1}
        max={99}
        onChange={(next) => onQuantityChange?.(next)}
        disabled={item.checked || !onQuantityChange}
      />

      <View style={styles.priceCell}>
        {totalPrice !== null ? (
          <Text
            style={[styles.price, item.checked && styles.priceDone]}
          >
            {fmtPrice(totalPrice)}
          </Text>
        ) : item.quantity > 1 ? (
          <Text style={styles.qty}>×{item.quantity}</Text>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 11,
    paddingHorizontal: 12,
    borderLeftWidth: 3,
    // No marginBottom + no borderRadius : rows merge into a single block.
    // The parent items-block container owns the rounded corners +
    // `overflow: 'hidden'` (cf `liste.tsx` `styles.itemsBlock`).
  },
  rowDivider: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: 'rgba(255,255,255,0.05)',
  },
  checkboxHit: {
    width: 24,
    height: 24,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkbox: {
    width: 24,
    height: 24,
    borderRadius: 8,
    borderWidth: 1.5,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkboxEmpty: {
    backgroundColor: 'rgba(0,0,0,0.2)',
    borderColor: 'rgba(255,255,255,0.18)',
  },
  tick: {
    color: 'rgba(0,0,0,0.7)',
    fontSize: 12,
    fontWeight: '900',
    lineHeight: 12,
  },
  iconTile: {
    width: 26,
    height: 26,
    borderRadius: 7,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconTxt: {
    fontSize: 13,
  },
  body: {
    flex: 1,
    minWidth: 0,
  },
  brand: {
    fontSize: 9,
    fontWeight: '800',
    color: 'rgba(255,255,255,0.4)',
    letterSpacing: 0.6,
    textTransform: 'uppercase',
    marginBottom: 1,
  },
  name: {
    fontSize: 13,
    fontWeight: '800',
    color: '#fff',
    letterSpacing: -0.2,
  },
  nameDone: {
    textDecorationLine: 'line-through',
    color: 'rgba(255,255,255,0.4)',
  },
  priceCell: {
    minWidth: 46,
    alignItems: 'flex-end',
  },
  price: {
    fontSize: 13,
    fontWeight: '900',
    color: Colors.gold,
    letterSpacing: -0.3,
  },
  priceDone: {
    color: 'rgba(255,255,255,0.35)',
  },
  qty: {
    fontSize: 11,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.55)',
  },
});

export default ListItemRow;
