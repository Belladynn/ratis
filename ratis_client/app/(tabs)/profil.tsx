// ratis_client/app/(tabs)/profil.tsx
//
// V5 Profil composition — port of `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 562-627 (`ProfilScreen`). Reads from existing hooks ; no new
// business logic introduced (R31, R33).
//
// Layout :
//   1. ScreenBackground (V5 industrial dark teal)
//   2. AppHeader (sticky : Saison · Niv. {n} + CAB pill + 3 icon buttons).
//      The JSX `ProfilScreen` itself does not render the AppHeader, but the
//      V5 screenshot (`Profil.png`) shows the same sticky band as the
//      Dashboard. Per ARCH `frontend_strict_iso.md` the screenshot is the
//      tiebreaker → kept here for visual iso.
//   3. PageTitleBand "Profil" + ⚙ settings btn (right slot).
//   4. Scrollable content :
//      a. ProfilAvatarSection (gradient circle + name + handle + level pill)
//      b. ProfilStatsGrid (3 tiles : CAB · SCANS · ÉCONOMIES)
//      c. ProfilMenuGroup "Récompenses" (Boutique / Succès / Parrainage)
//      d. ProfilMenuGroup "Compte" (Mes informations / Notifications)
//      e. ProfilMenuGroup "Session" (Se déconnecter — danger accent)
//      f. SupportIdCard — non-PII identifier the user can share with support
//      g. Footer trio "Ratis v1.0.0 · Made with 🧀"
//
// Nav routes (consume, don't invent) :
//   - Boutique         → V2 stub, disabled (greyed)
//   - Succès           → V2 stub, disabled (chunk 7 wires the modal)
//   - Parrainage       → router.push('/referral') (existing screen)
//   - Mes informations → router.push('/my-info') (existing screen)
//   - Notifications    → V2 stub, disabled
//   - Se déconnecter   → AuthContext.signOut()
//
// V1 notes :
//   - Achievements live count comes from `useAchievements()` (PR 8/8). The
//     subtitle reads `{unlocked} / {total} débloqués` from the merged
//     catalog buckets. No hardcoded count anymore.
//   - The "@handle" is derived from the user's email local-part (e.g.
//     `alice@…` → `@alice`). The User type has no dedicated handle field —
//     this is iso to the JSX which calls the user "@marie.l" without
//     surfacing a backend field for it.

import React, { useCallback, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { router } from 'expo-router';

import { Colors } from '@/constants/theme';
import { ScreenBackground } from '@/components/ui/screen-background';
import { AppHeader } from '@/components/dashboard/app-header';
import { PageTitleBand } from '@/components/ui/page-title-band';
import { ProfilAvatarSection } from '@/components/profil/profil-avatar-section';
import { ProfilStatsGrid } from '@/components/profil/profil-stats-grid';
import { ProfilMenuGroup } from '@/components/profil/profil-menu-group';
import { ProfilMenuRow } from '@/components/profil/profil-menu-row';
import { SupportIdCard } from '@/components/profil/SupportIdCard';
import { AchievementsModal } from '@/components/profil/achievements-modal';
import { MissionsModal } from '@/components/dashboard/missions-modal';

import { useAuth } from '@/hooks/useAuth';
import { useAuthMe } from '@/hooks/use-auth-me';
import { useCabBalance } from '@/hooks/use-cab-balance';
import { useAccountStats } from '@/hooks/use-account-stats';
import { useBattlepass } from '@/hooks/use-battlepass';
import { useAchievements } from '@/hooks/use-achievements';
import { useMissions } from '@/hooks/use-missions';
import { useTapBurst } from '@/hooks/use-tap-burst';
import { flattenAchievementsList } from '@/components/profil/achievements-adapter';
import { resetProfileTabBadge } from '@/services/achievement-notification-handler';
import { triggerSecretEvent } from '@/services/rewards-client';

/** Derive a user handle from the email local-part (`alice@x` → `@alice`). */
function deriveHandle(
  email: string | undefined,
  displayName: string | null | undefined,
): string {
  if (email) {
    const local = email.split('@')[0];
    if (local) return `@${local}`;
  }
  if (displayName) {
    return `@${displayName.toLowerCase().replace(/\s+/g, '.')}`;
  }
  return '@ratis';
}

export default function ProfilScreen() {
  const insets = useSafeAreaInsets();
  const auth = useAuth();
  const me = useAuthMe();

  const cab = useCabBalance();
  const stats = useAccountStats();
  const battlepass = useBattlepass();

  const user = me.data ?? null;
  const displayName = user?.display_name ?? 'Ratis';
  const handle = useMemo(
    () => deriveHandle(user?.email, user?.display_name),
    [user?.email, user?.display_name],
  );
  const level = battlepass.data?.current_level ?? 0;

  const scanCount = stats.data?.total_scans ?? 0;
  const savingsEuros = Math.round((stats.data?.total_savings_cents ?? 0) / 100);

  const handleLogout = useCallback(() => {
    void auth.signOut();
  }, [auth]);

  const handleNavMyInfo = useCallback(() => {
    router.push('/my-info');
  }, []);

  const handleNavReferral = useCallback(() => {
    router.push('/referral');
  }, []);

  const handleNavShop = useCallback(() => {
    router.push('/shop');
  }, []);

  const handleNavLeaderboard = useCallback(() => {
    router.push('/leaderboard');
  }, []);

  // Achievements — live data via `useAchievements()`. Opening the modal
  // also resets the profile tab badge counter (the user has now "seen" the
  // unlocks).
  const achievementsQuery = useAchievements();
  const liveAchievements = useMemo(
    () => flattenAchievementsList(achievementsQuery.data),
    [achievementsQuery.data],
  );
  const achievementsUnlocked = useMemo(
    () => liveAchievements.filter((a) => a.status === 'unlocked').length,
    [liveAchievements],
  );
  const achievementsTotal = liveAchievements.length;

  const [achievementsOpen, setAchievementsOpen] = useState(false);
  const handleNavAchievements = useCallback(() => {
    setAchievementsOpen(true);
    void resetProfileTabBadge();
  }, []);
  const closeAchievements = useCallback(() => setAchievementsOpen(false), []);

  // Missions modal — opens from the header calendar icon. Mirrors the
  // wiring on the dashboard so the user can reach the missions list from
  // any screen that shows the AppHeader.
  const missions = useMissions();
  const [missionsModalOpen, setMissionsModalOpen] = useState(false);
  const openMissionsModal = useCallback(
    () => setMissionsModalOpen(true),
    [],
  );
  const closeMissionsModal = useCallback(
    () => setMissionsModalOpen(false),
    [],
  );

  const activeMissionsCount = useMemo(() => {
    if (!missions.data) return 0;
    return (
      missions.data.daily.missions.filter((m) => m.status === 'active').length +
      missions.data.weekly.missions.filter((m) => m.status === 'active').length
    );
  }, [missions.data]);

  // V1.1 Konami secret — tap the avatar 5 times within 1.5s. Discrete, no UI
  // feedback (it's a secret). The unlock decision is server-side ; we just
  // forward the gesture as a `secret-event`. Fire-and-forget — a 401 / 403
  // / network error is fine, the user simply sees nothing.
  // See SA_DEV.md § "recurring patterns" — fire-and-forget for any
  // best-effort RPC.
  const fireKonamiSecret = useCallback(() => {
    triggerSecretEvent('konami_code_entered').catch(() => {
      // Silent — server-side rate limit / unknown event / network all OK.
    });
  }, []);
  const avatarTapBurst = useTapBurst({
    threshold: 5,
    windowMs: 1500,
    onComplete: fireKonamiSecret,
  });

  // No-op kept explicit so the intent reads at the call site (rather than
  // passing `undefined`).
  const noop = useCallback(() => {}, []);

  const settingsButton = (
    <Pressable
      testID="profil-settings-btn"
      accessibilityRole="button"
      accessibilityLabel="Réglages"
      onPress={noop}
      hitSlop={8}
      style={styles.headerIconBtn}
    >
      <Text style={styles.headerIconChar}>⚙</Text>
    </Pressable>
  );

  return (
    <View style={styles.container} testID="profil-screen">
      <ScreenBackground />

      <AppHeader
        achievementsBadge={achievementsTotal}
        calendarBadge={Math.min(99, activeMissionsCount)}
        onShop={handleNavShop}
        onAchievements={handleNavAchievements}
        onCalendar={openMissionsModal}
      />

      <PageTitleBand title="Profil" rightIcons={[settingsButton]} />

      <ScrollView
        contentContainerStyle={[
          styles.content,
          { paddingBottom: 96 + insets.bottom },
        ]}
        showsVerticalScrollIndicator={false}
      >
        <ProfilAvatarSection
          name={displayName}
          handle={handle}
          level={level}
          onPressAvatar={avatarTapBurst.register}
        />

        <ProfilStatsGrid
          cabBalance={cab.balance}
          scanCount={scanCount}
          savingsEuros={savingsEuros}
        />

        <ProfilMenuGroup label="Récompenses" accent="rewards">
          <ProfilMenuRow
            testID="profil-row-shop"
            icon="🎁"
            iconColor="gold"
            title="Boutique"
            // Bug 8 (PO ticket 2026-05-12) — Boutique is alpha-unavailable
            // (V1 stub : Runa KYB pending, gift-card provider not wired).
            // We grey out the row + disable the press so users see the
            // feature is acknowledged but not yet shippable. The subtitle
            // surfaces the V1 status explicitly.
            subtitle="Bientôt disponible"
            disabled
            onPress={handleNavShop}
          />
          <ProfilMenuRow
            testID="profil-row-achievements"
            icon="🏆"
            iconColor="violet"
            title="Succès"
            // When the achievements catalogue is still loading (or the
            // server returns an empty list during the very first session),
            // we surface an inviting subtitle rather than the previous
            // « Bientôt » copy which read as "disabled" to alpha testers
            // (PO ticket 2026-05-12 — Bug 3).
            subtitle={
              achievementsTotal > 0
                ? `${achievementsUnlocked} / ${achievementsTotal} débloqués`
                : 'Découvre tes succès'
            }
            onPress={handleNavAchievements}
          />
          <ProfilMenuRow
            testID="profil-row-referral"
            icon="👥"
            iconColor="red"
            title="Parrainage"
            subtitle="Invite un ami · +500 cab"
            onPress={handleNavReferral}
          />
          <ProfilMenuRow
            testID="profil-row-leaderboard"
            icon="🏁"
            iconColor="gold"
            title="Leaderboard"
            subtitle="Burst · mensuel + all-time"
            onPress={handleNavLeaderboard}
            last
          />
        </ProfilMenuGroup>

        <ProfilMenuGroup label="Compte" accent="account">
          <ProfilMenuRow
            testID="profil-row-my-info"
            icon="📝"
            iconColor="red"
            title="Mes informations"
            onPress={handleNavMyInfo}
          />
          <ProfilMenuRow
            testID="profil-row-notifications"
            icon="🔔"
            iconColor="gold"
            title="Notifications"
            onPress={noop}
            disabled
            last
          />
        </ProfilMenuGroup>

        <ProfilMenuGroup label="Session" accent="danger">
          <ProfilMenuRow
            testID="profil-row-logout"
            icon="🚪"
            iconColor="red"
            title="Se déconnecter"
            onPress={handleLogout}
            danger
            last
          />
        </ProfilMenuGroup>

        {user?.support_id ? (
          <View style={styles.supportIdWrap}>
            <SupportIdCard support_id={user.support_id} />
          </View>
        ) : null}

        <Text testID="profil-version-trio" style={styles.versionTrio}>
          Ratis v1.0.0 · Made with 🧀
        </Text>
      </ScrollView>

      <AchievementsModal
        open={achievementsOpen}
        onClose={closeAchievements}
        achievements={liveAchievements}
      />

      <MissionsModal
        open={missionsModalOpen}
        onClose={closeMissionsModal}
        weekly={missions.data?.weekly.missions ?? []}
        daily={missions.data?.daily.missions ?? []}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  content: {
    paddingHorizontal: 14,
    paddingTop: 4,
    gap: 16,
  },
  headerIconBtn: {
    width: 32,
    height: 32,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 10,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.1)',
  },
  headerIconChar: {
    fontSize: 14,
    color: Colors.textPrimary,
    fontWeight: '800',
  },
  supportIdWrap: {
    marginTop: 4,
  },
  versionTrio: {
    textAlign: 'center',
    fontSize: 10,
    fontWeight: '600',
    color: 'rgba(255,255,255,0.3)',
    marginTop: 8,
  },
});
