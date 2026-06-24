// ratis_client/app/(tabs)/index.tsx
//
// V5 Dashboard composition — port of `Ratis_handoff/lib/ratis-real-v4.jsx`
// lines 1078-1175. The visual layer reads from existing hooks ; no new
// business logic is introduced here (R31).
//
// Layout :
//   1. ScreenBackground (V5 shared)
//   2. AppHeader (sticky, season XP + CAB pill + 3 icon buttons)
//   3. Scrollable content :
//      a. Greeting "Bonjour" + contextual line
//      b. Hero row (1.4 / 1.0) :
//         - Left  : JarPrestige
//         - Right : MysteryProductCard + JackStreakButton stacked
//      c. NextAchievementCard (V1 placeholder data — V2 hook lands chunk 7)
//      d. BattlepassCard
//      e. MissionsBlock (weekly + daily, with chest overlay)
//      f. EnrichissementCard
//
// V1.1 settings notes :
//   - The jar's `monthly_subscription_price_cents` is sourced from
//     `useRatisSettings()` (=> `pipeline.jar.monthly_subscription_price_cents`),
//     with a 999 cents fallback used until the settings query resolves
//     (or if the backend ships an empty whitelist payload).
//   - The legacy `stats.data?.rings.subscription_price_cents` from
//     `useAccountStats()` is kept as a secondary fallback so the dashboard
//     keeps rendering during a brief network partition (the rings model
//     was the source of truth in V1.0).
//   - Achievements badge value (21) and calendar badge value (5) are V1
//     hardcoded placeholders — they're not yet sourced from hooks.
//     `useMissions` is consulted for an active count fallback.

import React, { useCallback, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';

import { Colors, Typography } from '@/constants/theme';
import { ScreenBackground } from '@/components/ui/screen-background';
import { AppHeader } from '@/components/dashboard/app-header';
import { JarPrestige } from '@/components/dashboard/jar-prestige';
import { MysteryProductCard } from '@/components/dashboard/mystery-product-card';
import { JackStreakButton } from '@/components/dashboard/jack-streak-button';
import {
  NextAchievementCard,
  type NextAchievement,
} from '@/components/dashboard/next-achievement-card';
import { BattlepassCard } from '@/components/dashboard/battlepass-card';
import { MissionsBlock } from '@/components/dashboard/missions-block';
import { MissionsModal } from '@/components/dashboard/missions-modal';
import { BufferConfirmModal } from '@/components/dashboard/buffer-confirm-modal';
import { EnrichissementCard } from '@/components/dashboard/enrichissement-card';
import { AchievementsModal } from '@/components/profil/achievements-modal';
import { flattenAchievementsList } from '@/components/profil/achievements-adapter';
import { router } from 'expo-router';
import { useStreak } from '@/hooks/use-streak';
import { useFeedJack } from '@/hooks/use-feed-jack';
import { useMissions, useClaimMission } from '@/hooks/use-missions';
import { useBufferMission } from '@/hooks/use-buffer-mission';
import { useClaimBurst } from '@/hooks/use-claim-burst';
import { useBattlepass } from '@/hooks/use-battlepass';
import { useEnrichissement } from '@/hooks/use-enrichissement';
import { useAccountStats } from '@/hooks/use-account-stats';
import { useAchievements } from '@/hooks/use-achievements';
import { useRatisSettings } from '@/hooks/use-ratis-settings';
import type { DailyMission } from '@/types/gamification';

// Last-resort static fallback : used only if both `useRatisSettings()` and
// `useAccountStats()` are still loading on the very first render. Once
// either query resolves we read the live value (cf jar derivations below).
const JAR_MONTHLY_PRICE_CENTS_FALLBACK = 999;

function getContextualMessage(hour: number, streak: number): string {
  if (hour < 6) return 'Tu veilles tard ce soir 🦉';
  if (hour < 12) return 'Belle matinée pour économiser';
  if (hour < 18)
    return streak >= 7
      ? `Belle série de ${streak} jours !`
      : 'Continue, tu es sur la bonne voie';
  return 'Bilan de la journée';
}

// V1 placeholder achievement (the achievements backend lands V2). The
// component silently renders nothing if `achievement` is null, so this
// stays safe even when we eventually source it from a hook.
const PLACEHOLDER_ACHIEVEMENT: NextAchievement = {
  id: 'demi-bil',
  label: 'Demi-bilan',
  rarity: 'rare',
  progress: 47,
  target: 50,
  status: 'in_progress',
  icon: '🥈',
};

export default function DashboardScreen() {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const streak = useStreak();
  const missions = useMissions();
  const battlepass = useBattlepass();
  const enrichissement = useEnrichissement();
  const stats = useAccountStats();
  const ratisSettings = useRatisSettings();
  const claimMission = useClaimMission();
  const bufferMission = useBufferMission();
  const claimBurst = useClaimBurst();
  const feedJack = useFeedJack();

  const [missionsModalOpen, setMissionsModalOpen] = useState(false);
  const openMissionsModal = useCallback(() => setMissionsModalOpen(true), []);
  const closeMissionsModal = useCallback(() => setMissionsModalOpen(false), []);

  // Achievements modal — opens from the header trophy icon. Lives at the
  // dashboard level (and is also mounted on `profil.tsx`) so the user can
  // reach the modal from either screen without leaving the current context.
  const [achievementsModalOpen, setAchievementsModalOpen] = useState(false);
  const openAchievementsModal = useCallback(
    () => setAchievementsModalOpen(true),
    [],
  );
  const closeAchievementsModal = useCallback(
    () => setAchievementsModalOpen(false),
    [],
  );
  const achievementsQuery = useAchievements();
  const liveAchievements = useMemo(
    () => flattenAchievementsList(achievementsQuery.data),
    [achievementsQuery.data],
  );

  // Header shop icon → /shop screen. Kept here so the icon button is not a
  // dead end (it was wired in the JSX source but never plumbed to a route
  // when the V5 header was ported).
  const handleShopPress = useCallback(() => {
    router.push('/shop');
  }, []);
  const handleClaim = useCallback(
    (id: string) => claimMission.mutate(id),
    [claimMission],
  );

  // Buffer confirm modal state — pending mission rendered while user
  // confirms, then mutation fires and modal closes regardless of result.
  // Errors bubble through the mutation `error` field — UI surface is
  // owned by a future Toast layer (cf KP `feed_jack` on toast bus).
  const [pendingBuffer, setPendingBuffer] = useState<DailyMission | null>(null);
  const handleBufferPress = useCallback(
    (m: DailyMission) => setPendingBuffer(m),
    [],
  );
  const closeBufferModal = useCallback(() => setPendingBuffer(null), []);
  const confirmBuffer = useCallback(() => {
    if (!pendingBuffer) return;
    bufferMission.mutate(pendingBuffer.id, {
      onSettled: () => setPendingBuffer(null),
    });
  }, [pendingBuffer, bufferMission]);

  const handleBurstClaim = useCallback(
    (id: string) => claimBurst.mutate(id),
    [claimBurst],
  );

  // Bug 7 (PO ticket 2026-05-12) — wire the "Feed Jack" CTA. The
  // JackStreakButton previously received `onFeed=undefined`, so the
  // Pressable on the right column rendered but did nothing on press
  // (looked « dead » to PO). The mutation invalidates ['streak'],
  // ['battlepass'] and ['cab-balance'] so the UI refreshes the streak
  // ribbon, the XP gauge and the CAB pill in one round-trip.
  const handleFeedJack = useCallback(() => {
    if (feedJack.isPending) return;
    feedJack.mutate();
  }, [feedJack]);

  // Buffer modal copy needs the per-Buffer R bonus (= cab_reward / (n+1))
  // so the user knows exactly how much CAB this Buffer unlocks.
  const bufferCabBonus = pendingBuffer
    ? Math.max(
        1,
        Math.round(
          pendingBuffer.cab_reward / ((pendingBuffer.buffer_count ?? 0) + 1),
        ),
      )
    : 0;

  const hour = new Date().getHours();
  const message = getContextualMessage(hour, streak.data?.streak_days ?? 0);

  // Active missions count → AppHeader calendar badge.
  const activeMissionsCount = useMemo(() => {
    if (!missions.data) return 0;
    return (
      missions.data.daily.missions.filter((m) => m.status === 'active').length +
      missions.data.weekly.missions.filter((m) => m.status === 'active').length
    );
  }, [missions.data]);

  // Jar derivations.
  // Source of truth for the monthly subscription price : `useRatisSettings()`
  // (`pipeline.jar.monthly_subscription_price_cents`). The legacy account-stats
  // ring value is kept as a transitional fallback so a brief network partition
  // doesn't break the dashboard.
  const totalSavedCents = stats.data?.total_savings_cents ?? 0;
  const settingsJarPrice = ratisSettings.data?.[
    'pipeline.jar.monthly_subscription_price_cents'
  ] as number | undefined;
  const monthlyPriceCents =
    settingsJarPrice ??
    stats.data?.rings.subscription_price_cents ??
    JAR_MONTHLY_PRICE_CENTS_FALLBACK;
  // Use the rings model's totalAbonnements to derive currentFill into
  // the [0, 100] range — fill_pct is the fractional part of the current
  // abonnement.
  const totalAbonnements =
    monthlyPriceCents > 0 ? totalSavedCents / monthlyPriceCents : 0;
  const completedAbonnements = Math.floor(totalAbonnements);
  const currentFill = (totalAbonnements - completedAbonnements) * 100;
  const prestigeLevel = Math.floor(completedAbonnements / 10);
  const nextTierRemainingCents = Math.max(
    0,
    monthlyPriceCents - (totalSavedCents % monthlyPriceCents),
  );

  return (
    <View style={{ flex: 1, backgroundColor: Colors.bg }} testID="dashboard-screen">
      <ScreenBackground />
      <AppHeader
        achievementsBadge={21}
        calendarBadge={Math.min(99, activeMissionsCount)}
        onShop={handleShopPress}
        onAchievements={openAchievementsModal}
        onCalendar={openMissionsModal}
      />
      <ScrollView
        testID="dashboard-scroll"
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[
          styles.scrollContent,
          { paddingBottom: insets.bottom + 100 },
        ]}
      >
        {/* Greeting (sits above the hero row, JSX 1133-1137) */}
        <View style={styles.greeting}>
          <Text style={styles.greetingHello}>Bonjour,</Text>
          <Text style={styles.greetingMessage}>{message}</Text>
        </View>

        {/* Hero row */}
        <View style={styles.heroRow}>
          <View style={styles.heroLeft}>
            <JarPrestige
              currentFill={currentFill}
              prestigeLevel={prestigeLevel}
              totalSaved={totalSavedCents}
              nextTierRemainingCents={nextTierRemainingCents}
            />
          </View>
          <View style={styles.heroRight}>
            <MysteryProductCard />
            <JackStreakButton
              streak={streak.data}
              isLoading={streak.isLoading}
              onFeed={handleFeedJack}
            />
          </View>
        </View>

        {/* Next achievement preview */}
        <NextAchievementCard achievement={PLACEHOLDER_ACHIEVEMENT} />

        {/* Battle pass */}
        <BattlepassCard
          battlepass={battlepass.data}
          isLoading={battlepass.isLoading}
        />

        {/* Missions */}
        <MissionsBlock
          weekly={missions.data?.weekly.missions ?? []}
          daily={missions.data?.daily.missions ?? []}
          onClaim={handleClaim}
          onBufferPress={handleBufferPress}
          onBurstClaim={handleBurstClaim}
        />
        <Pressable
          testID="dashboard-missions-see-all"
          accessibilityRole="button"
          accessibilityLabel={t('dashboard.missions.see_all_a11y')}
          onPress={openMissionsModal}
          hitSlop={6}
          style={styles.seeAllRow}
        >
          <Text style={styles.seeAllText}>
            {t('dashboard.missions.see_all')}
          </Text>
        </Pressable>

        {/* Enrichissement */}
        <EnrichissementCard
          task={enrichissement.data}
          isLoading={enrichissement.isLoading}
        />
      </ScrollView>

      <MissionsModal
        open={missionsModalOpen}
        onClose={closeMissionsModal}
        weekly={missions.data?.weekly.missions ?? []}
        daily={missions.data?.daily.missions ?? []}
        onClaim={handleClaim}
        onBufferPress={handleBufferPress}
        onBurstClaim={handleBurstClaim}
      />

      <BufferConfirmModal
        open={pendingBuffer !== null}
        onClose={closeBufferModal}
        onConfirm={confirmBuffer}
        cabBonus={bufferCabBonus}
        currentBufferCount={pendingBuffer?.buffer_count ?? 0}
        loading={bufferMission.isPending}
      />

      <AchievementsModal
        open={achievementsModalOpen}
        onClose={closeAchievementsModal}
        achievements={liveAchievements}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  scrollContent: {
    paddingHorizontal: 12,
    paddingTop: 12,
    gap: 12,
  },
  greeting: {
    paddingHorizontal: 4,
  },
  greetingHello: {
    ...Typography.bodySm,
    color: Colors.textSecondary,
    fontSize: 11,
    letterSpacing: -0.1,
  },
  greetingMessage: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 17,
    color: Colors.textPrimary,
    letterSpacing: -0.34,
    marginTop: 2,
  },
  heroRow: {
    flexDirection: 'row',
    gap: 10,
    alignItems: 'stretch',
  },
  heroLeft: {
    flex: 1.4,
    minHeight: 220,
  },
  heroRight: {
    flex: 1,
    flexDirection: 'column',
    gap: 10,
  },
  seeAllRow: {
    alignSelf: 'flex-end',
    paddingHorizontal: 6,
    paddingVertical: 4,
    marginTop: -4,
  },
  seeAllText: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 11,
    color: Colors.terracotta,
    letterSpacing: 0.4,
    textTransform: 'uppercase',
  },
});
