// ratis_client/app/shop/[brand_id].tsx
//
// Boutique V1 — denominations screen for a single brand + confirm modal.
//
// UX (cf ARCH_boutique.md § UX flow) :
//   ┌────────────────────────────┐
//   │ ← Amazon.fr                │
//   │ Carte 5€   25 000 CAB ✓    │
//   │ Carte 10€  50 000 CAB ✓    │
//   │ Carte 20€  100 000 CAB ✓   │
//   │ Carte 50€  250 000 CAB ✗   │ (greyed if balance < cost)
//   │ Tu as fait 0€/100€ aujd    │
//   │ Tu as fait 50€/300€ sem    │
//   └────────────────────────────┘
//
// Tap dénomination dispo → confirm modal → POST → success → return to Profil.
//
// V1.1 usage stats sourcing :
//   - Daily/weekly cap usage → `useGiftCardCapUsage()` (server-side
//     SUM over gift_card_orders, see ARCH_boutique.md § Caps).
//   - Per-brand aggregate (orders_count, total_saved_cents) →
//     `useShopUsageStats(brand_id)`.
// Both replace the legacy client-side `computeUsageStats` reducer that
// walked the (paginated, partial) gift-cards list.

import React, { useCallback, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { Button, Modal } from '@/components/design-system';
import { Colors, Radii, Spacing } from '@/constants/theme';
import { useShopCatalog, type ShopBrand } from '@/hooks/use-shop-catalog';
import { useShopOrder } from '@/hooks/use-shop-order';
import { useGiftCardCapUsage } from '@/hooks/use-gift-card-cap-usage';
import { useShopUsageStats } from '@/hooks/use-shop-usage-stats';
import { useCabBalance } from '@/hooks/use-cab-balance';
import {
  CAB_PER_EUR,
  V1_DENOMINATIONS_CENTS,
  cabCostFor,
} from '@/types/shop';

// Map AuthError-like detail strings → i18n keys.
const ERROR_KEY_MAP: Record<string, string> = {
  insufficient_cab_balance: 'shop.errors.insufficient_cab_balance',
  daily_redeem_cap_reached: 'shop.errors.daily_redeem_cap_reached',
  weekly_redeem_cap_reached: 'shop.errors.weekly_redeem_cap_reached',
  annual_gift_card_cap_reached: 'shop.errors.annual_gift_card_cap_reached',
  brand_not_available: 'shop.errors.brand_not_available',
  invalid_brand_id: 'shop.errors.brand_not_available',
  invalid_denomination: 'shop.errors.invalid_denomination',
  duplicate_order_recent: 'shop.errors.duplicate_order_recent',
};

export default function ShopBrandScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const { brand_id } = useLocalSearchParams<{ brand_id: string }>();

  const catalog = useShopCatalog();
  const capUsage = useGiftCardCapUsage();
  const brandStats = useShopUsageStats(brand_id);
  const cab = useCabBalance();
  const order = useShopOrder();

  const brand = useMemo<ShopBrand | undefined>(
    () => catalog.data?.brands.find((b) => b.id === brand_id),
    [catalog.data, brand_id],
  );

  // Cap-display defaults (used while the cap-usage query is still loading
  // — every value is replaced by the server payload as soon as it lands).
  const dailyUsedCents = capUsage.data?.daily_cents ?? 0;
  const weeklyUsedCents = capUsage.data?.weekly_cents ?? 0;
  const dailyCapCents = capUsage.data?.daily_cap_cents ?? 10_000;
  const weeklyCapCents = capUsage.data?.weekly_cap_cents ?? 30_000;

  const [pending, setPending] = useState<{
    denomination_cents: number;
  } | null>(null);

  const handleBack = useCallback(() => {
    router.back();
  }, [router]);

  const handleSelect = useCallback((denomination_cents: number) => {
    setPending({ denomination_cents });
  }, []);

  const closeModal = useCallback(() => {
    if (order.isPending) return; // don't allow cancel mid-flight
    setPending(null);
  }, [order.isPending]);

  const confirmPurchase = useCallback(async () => {
    if (!pending || !brand) return;
    try {
      await order.mutateAsync({
        brand_id: brand.id,
        denomination_cents: pending.denomination_cents,
      });
      setPending(null);
      // Send the user back to the profil tab — that's where the future
      // "Mes cartes cadeaux" entry lives. Toast / nav to a dedicated
      // screen lands in V1.x.
      Alert.alert(t('shop.success_title'), t('shop.success_body'), [
        {
          text: t('shop.success_ok'),
          onPress: () => router.push('/(tabs)/profil'),
        },
      ]);
    } catch (e: unknown) {
      // The api-client throws AuthError with `code` (= detail). Use it,
      // fallback to a generic message.
      const detail =
        (e as { code?: string }).code ?? (e as Error).message ?? 'unknown';
      const key = ERROR_KEY_MAP[detail] ?? 'shop.errors.generic';
      Alert.alert(t('shop.error_title'), t(key));
    }
  }, [pending, brand, order, router, t]);

  // ── Loading / not-found gates ─────────────────────────────────────────────
  if (catalog.isLoading) {
    return (
      <View style={styles.container}>
        <SafeAreaView style={styles.center} edges={['top']}>
          <ActivityIndicator color={Colors.terracotta} />
        </SafeAreaView>
      </View>
    );
  }
  if (!brand) {
    return (
      <View style={styles.container}>
        <SafeAreaView style={styles.center} edges={['top']}>
          <Text style={styles.errorTxt}>{t('shop.brand_not_found')}</Text>
          <Pressable style={styles.retryBtn} onPress={handleBack}>
            <Text style={styles.retryTxt}>{t('shop.back')}</Text>
          </Pressable>
        </SafeAreaView>
      </View>
    );
  }

  const balance = cab.balance;
  const dailyCapHit = dailyUsedCents >= dailyCapCents;
  const weeklyCapHit = weeklyUsedCents >= weeklyCapCents;

  return (
    <View style={styles.container} testID="shop-brand-screen">
      <SafeAreaView edges={['top']} style={{ flex: 1 }}>
        <View style={styles.header}>
          <Pressable
            testID="shop-brand-back"
            accessibilityRole="button"
            accessibilityLabel={t('shop.back')}
            onPress={handleBack}
            style={styles.backBtn}
          >
            <Text style={styles.backTxt}>‹</Text>
          </Pressable>
          <Text style={styles.title} numberOfLines={1}>
            {brand.name}
          </Text>
          <View style={styles.backBtn} />
        </View>

        <ScrollView
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.balanceCard}>
            <Text style={styles.balanceLabel}>{t('shop.balance_label')}</Text>
            <Text style={styles.balanceValue}>{formatCab(balance)} CAB</Text>
          </View>

          <View style={styles.list} testID="shop-denominations-list">
            {V1_DENOMINATIONS_CENTS.map((denom) => {
              const cabCost = cabCostFor(denom);
              const affordable = balance >= cabCost;
              const wouldExceedDaily =
                dailyUsedCents + denom > dailyCapCents;
              const wouldExceedWeekly =
                weeklyUsedCents + denom > weeklyCapCents;
              const blocked =
                !affordable || wouldExceedDaily || wouldExceedWeekly;
              return (
                <DenominationRow
                  key={denom}
                  denomination_cents={denom}
                  cab_cost={cabCost}
                  affordable={affordable}
                  blocked={blocked}
                  onPress={() => handleSelect(denom)}
                  label={t('shop.denomination_label', {
                    eur: denom / 100,
                  })}
                  cabLabel={t('shop.denomination_cost', {
                    cab: formatCab(cabCost),
                  })}
                />
              );
            })}
          </View>

          {/* Caps usage (server-authoritative — see useGiftCardCapUsage) */}
          <View style={styles.capsBox} testID="shop-caps">
            <CapsLine
              label={t('shop.caps_daily', {
                used: formatEur(dailyUsedCents),
                cap: formatEur(dailyCapCents),
              })}
              warn={dailyCapHit}
            />
            <CapsLine
              label={t('shop.caps_weekly', {
                used: formatEur(weeklyUsedCents),
                cap: formatEur(weeklyCapCents),
              })}
              warn={weeklyCapHit}
            />
            <Text style={styles.capsNote} testID="shop-caps-mvp-note">
              {t('shop.caps_mvp_note')}
            </Text>
          </View>

          {/* Per-brand history line — only when the user has previous orders. */}
          {brandStats.data && brandStats.data.orders_count > 0 ? (
            <View style={styles.brandStatsBox} testID="shop-brand-stats">
              <Text style={styles.brandStatsLine}>
                {t('shop.brand_stats_line', {
                  count: brandStats.data.orders_count,
                  total: formatEur(brandStats.data.total_saved_cents),
                  brand: brand.name,
                })}
              </Text>
            </View>
          ) : null}
        </ScrollView>
      </SafeAreaView>

      <Modal
        open={pending !== null}
        onClose={closeModal}
        title={t('shop.confirm_title')}
        eyebrow={t('shop.confirm_eyebrow')}
        scrollable={false}
        testID="shop-confirm-modal"
      >
        {pending ? (
          <ConfirmBody
            brand={brand}
            denomination_cents={pending.denomination_cents}
            balance={balance}
            isPending={order.isPending}
            onConfirm={confirmPurchase}
            onCancel={closeModal}
          />
        ) : null}
      </Modal>
    </View>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function DenominationRow({
  denomination_cents,
  cab_cost,
  affordable,
  blocked,
  onPress,
  label,
  cabLabel,
}: {
  denomination_cents: number;
  cab_cost: number;
  affordable: boolean;
  blocked: boolean;
  onPress: () => void;
  label: string;
  cabLabel: string;
}) {
  return (
    <Pressable
      testID={`shop-denom-${denomination_cents}`}
      onPress={blocked ? undefined : onPress}
      disabled={blocked}
      accessibilityRole="button"
      accessibilityState={{ disabled: blocked }}
      style={[styles.row, blocked && styles.rowDisabled]}
    >
      <View style={styles.rowLeft}>
        <Text style={[styles.rowLabel, blocked && styles.rowLabelMuted]}>
          {label}
        </Text>
        <Text style={[styles.rowCab, blocked && styles.rowCabMuted]}>
          {cabLabel}
        </Text>
      </View>
      <Text
        style={[
          styles.rowMark,
          affordable ? styles.rowMarkOk : styles.rowMarkKo,
        ]}
        accessibilityElementsHidden
      >
        {affordable ? '✓' : '✗'}
      </Text>
    </Pressable>
  );
}

function CapsLine({ label, warn }: { label: string; warn: boolean }) {
  return (
    <Text style={[styles.capsLine, warn && styles.capsLineWarn]}>{label}</Text>
  );
}

function ConfirmBody({
  brand,
  denomination_cents,
  balance,
  isPending,
  onConfirm,
  onCancel,
}: {
  brand: ShopBrand;
  denomination_cents: number;
  balance: number;
  isPending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const cabCost = cabCostFor(denomination_cents);
  const after = balance - cabCost;

  return (
    <View style={styles.confirmBody}>
      <Text style={styles.confirmLine}>
        {t('shop.confirm_card_line', {
          brand: brand.name,
          eur: denomination_cents / 100,
        })}
      </Text>
      <View style={styles.confirmBlock}>
        <View style={styles.confirmRow}>
          <Text style={styles.confirmRowLabel}>{t('shop.confirm_cost')}</Text>
          <Text style={styles.confirmRowVal}>{formatCab(cabCost)} CAB</Text>
        </View>
        <View style={styles.confirmRow}>
          <Text style={styles.confirmRowLabel}>{t('shop.confirm_after')}</Text>
          <Text style={styles.confirmRowVal}>{formatCab(after)} CAB</Text>
        </View>
      </View>
      <View style={styles.confirmActions}>
        <Button
          testID="shop-confirm-cancel"
          variant="secondary"
          label={t('shop.confirm_cancel')}
          onPress={onCancel}
          disabled={isPending}
          fullWidth
        />
        <Button
          testID="shop-confirm-submit"
          variant="primary"
          label={t('shop.confirm_submit')}
          onPress={onConfirm}
          loading={isPending}
          fullWidth
        />
      </View>
    </View>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function formatCab(n: number): string {
  return n.toLocaleString('fr-FR').replace(/ /g, ' ');
}
function formatEur(cents: number): string {
  return `${Math.round(cents / 100)}€`;
}

// helper used at module scope (avoids unused-import on `CAB_PER_EUR`)
void CAB_PER_EUR;

// ─────────────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bg },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  backBtn: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
  },
  backTxt: { color: Colors.textPrimary, fontSize: 28, lineHeight: 28 },
  title: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '900',
    color: Colors.textPrimary,
    letterSpacing: -0.3,
  },
  content: { padding: 18, paddingBottom: 60, gap: 16 },
  balanceCard: {
    backgroundColor: 'rgba(255,184,0,0.10)',
    borderWidth: 1,
    borderColor: 'rgba(255,184,0,0.35)',
    borderRadius: Radii.card,
    padding: Spacing.lg,
    alignItems: 'center',
    gap: 4,
  },
  balanceLabel: {
    fontSize: 11,
    fontWeight: '800',
    color: 'rgba(255,255,255,0.55)',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  balanceValue: {
    fontSize: 24,
    fontWeight: '900',
    color: Colors.gold,
    letterSpacing: -0.4,
  },
  list: { gap: 10 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: Colors.surface,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: Radii.card,
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  rowDisabled: {
    opacity: 0.45,
  },
  rowLeft: { gap: 2 },
  rowLabel: {
    fontSize: 16,
    fontWeight: '900',
    color: Colors.textPrimary,
    letterSpacing: -0.2,
  },
  rowLabelMuted: { color: 'rgba(255,255,255,0.65)' },
  rowCab: {
    fontSize: 12,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.55)',
  },
  rowCabMuted: { color: 'rgba(255,255,255,0.35)' },
  rowMark: { fontSize: 22, fontWeight: '900' },
  rowMarkOk: { color: '#7DD3A4' },
  rowMarkKo: { color: '#EF4444' },
  capsBox: {
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderRadius: Radii.card,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.06)',
    padding: 14,
    gap: 4,
  },
  capsLine: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.7)',
    fontWeight: '700',
  },
  capsLineWarn: { color: '#EF4444' },
  capsNote: {
    fontSize: 10,
    color: 'rgba(255,255,255,0.35)',
    fontStyle: 'italic',
    marginTop: 4,
  },
  brandStatsBox: {
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderRadius: Radii.card,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.06)',
    padding: 14,
  },
  brandStatsLine: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.7)',
    fontWeight: '700',
  },
  confirmBody: { gap: 14 },
  confirmLine: {
    fontSize: 14,
    color: Colors.textPrimary,
    textAlign: 'center',
    fontWeight: '700',
  },
  confirmBlock: {
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.06)',
    borderRadius: Radii.card,
    padding: 14,
    gap: 8,
  },
  confirmRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  confirmRowLabel: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.55)',
    fontWeight: '700',
  },
  confirmRowVal: {
    fontSize: 14,
    fontWeight: '900',
    color: Colors.textPrimary,
  },
  confirmActions: {
    flexDirection: 'column',
    gap: 10,
    marginTop: 4,
  },
  errorTxt: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.7)',
    textAlign: 'center',
    paddingHorizontal: 24,
  },
  retryBtn: {
    backgroundColor: Colors.terracotta,
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: Radii.btn,
  },
  retryTxt: {
    color: Colors.textPrimary,
    fontWeight: '900',
    fontSize: 13,
  },
});
