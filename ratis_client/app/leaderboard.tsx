// ratis_client/app/leaderboard.tsx
//
// Burst Leaderboard screen — entry point for the Burst leaderboard
// (refonte 2026-05-09 Buffer + Burst).
//
// Two tabs : Mensuel (= current month UTC) + All-time. Each tab consumes
// `useBurstLeaderboard({ period })` which queries the backend's
// `/gamification/leaderboard/burst-{monthly,alltime}` endpoints. The
// caller's rank + max_xp are surfaced at the top of the screen so the
// user always sees where they stand even if they're not in the top 50.
//
// Reached via `router.push('/leaderboard')` from the Profil menu.

import React, { useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { Colors, Typography } from '@/constants/theme';
import { ScreenBackground } from '@/components/ui/screen-background-legacy';
import { SegmentedTabs } from '@/components/design-system';
import {
  useBurstLeaderboard,
  type BurstLeaderboardEntry,
} from '@/hooks/use-burst-leaderboard';
import { getMissionLabel } from '@/types/gamification';

type Period = 'monthly' | 'alltime';

export default function LeaderboardScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const [period, setPeriod] = useState<Period>('monthly');

  const query = useBurstLeaderboard({ period });

  const top = query.data?.top ?? [];
  const yourRank = query.data?.your_rank ?? null;
  const yourMaxXp = query.data?.your_max_xp ?? null;

  return (
    <View style={styles.container}>
      <ScreenBackground />
      <SafeAreaView edges={['top']} style={styles.safe}>
        {/* Header */}
        <View style={styles.header}>
          <Pressable
            testID="leaderboard-back"
            accessibilityRole="button"
            accessibilityLabel={t('gamification.leaderboard.back')}
            onPress={() => router.back()}
            style={styles.backBtn}
          >
            <Text style={styles.backTxt}>‹</Text>
          </Pressable>
          <Text style={styles.title}>
            {t('gamification.leaderboard.title')}
          </Text>
          <View style={styles.backBtn} />
        </View>

        {/* Tabs */}
        <View style={styles.tabsRow}>
          <SegmentedTabs
            testID="leaderboard-tabs"
            tabs={[
              {
                id: 'monthly',
                label: t('gamification.leaderboard.tab_monthly'),
              },
              {
                id: 'alltime',
                label: t('gamification.leaderboard.tab_alltime'),
              },
            ]}
            activeId={period}
            onChange={(id) => setPeriod(id as Period)}
          />
        </View>

        {/* User rank summary */}
        <View testID="leaderboard-your-rank" style={styles.yourRankCard}>
          <Text style={styles.yourRankPrimary}>
            {yourRank
              ? t('gamification.leaderboard.your_rank', { rank: yourRank })
              : t('gamification.leaderboard.your_rank_unranked')}
          </Text>
          {yourMaxXp !== null ? (
            <Text style={styles.yourRankSecondary}>
              {t('gamification.leaderboard.your_max_xp', {
                xp: yourMaxXp.toLocaleString('fr-FR'),
              })}
            </Text>
          ) : null}
        </View>

        {/* Body */}
        {query.isLoading ? (
          <View testID="leaderboard-loading" style={styles.center}>
            <ActivityIndicator color={Colors.terracotta} />
            <Text style={styles.centerText}>
              {t('gamification.leaderboard.loading')}
            </Text>
          </View>
        ) : query.isError ? (
          <View testID="leaderboard-error" style={styles.center}>
            <Text style={styles.errorTitle}>
              {t('gamification.leaderboard.error_title')}
            </Text>
            <Pressable
              testID="leaderboard-retry"
              style={styles.retryBtn}
              onPress={() => {
                void query.refetch();
              }}
            >
              <Text style={styles.retryTxt}>
                {t('common.retry')}
              </Text>
            </Pressable>
          </View>
        ) : top.length === 0 ? (
          <View testID="leaderboard-empty" style={styles.center}>
            <Text style={styles.emptyText}>
              {t('gamification.leaderboard.empty')}
            </Text>
          </View>
        ) : (
          <FlatList
            testID="leaderboard-list"
            data={top}
            keyExtractor={(item, idx) => `${item.user_id}-${idx}`}
            contentContainerStyle={styles.listContent}
            renderItem={({ item, index }) => (
              <LeaderboardRow
                rank={index + 1}
                entry={item}
                testID={`leaderboard-row-${index}`}
              />
            )}
            ItemSeparatorComponent={() => <View style={styles.separator} />}
          />
        )}
      </SafeAreaView>
    </View>
  );
}

function LeaderboardRow({
  rank,
  entry,
  testID,
}: {
  rank: number;
  entry: BurstLeaderboardEntry;
  testID: string;
}) {
  return (
    <View testID={testID} style={styles.row}>
      <Text style={styles.rowRank}>#{rank}</Text>
      <View style={styles.rowMid}>
        <Text style={styles.rowName} numberOfLines={1}>
          {entry.display_name}
        </Text>
        <Text style={styles.rowMission} numberOfLines={1}>
          {getMissionLabel(entry.mission_action_type)}
        </Text>
      </View>
      <View style={styles.rowRight}>
        <Text style={styles.rowXp}>
          {entry.xp_earned.toLocaleString('fr-FR')} XP
        </Text>
        <Text style={styles.rowBurst}>Burst × {entry.burst_count}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  safe: {
    flex: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  backBtn: {
    width: 36,
    height: 36,
    alignItems: 'center',
    justifyContent: 'center',
  },
  backTxt: {
    color: Colors.textPrimary,
    fontSize: 28,
    fontWeight: '700',
    lineHeight: 28,
  },
  title: {
    flex: 1,
    textAlign: 'center',
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 17,
    color: Colors.textPrimary,
    letterSpacing: -0.34,
  },
  tabsRow: {
    paddingHorizontal: 14,
    paddingTop: 4,
    paddingBottom: 8,
  },
  yourRankCard: {
    marginHorizontal: 14,
    marginBottom: 8,
    padding: 12,
    borderRadius: 14,
    backgroundColor: Colors.surface,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
  },
  yourRankPrimary: {
    ...Typography.body,
    fontFamily: 'Inter_800ExtraBold',
    color: Colors.terracotta,
    fontSize: 13,
    letterSpacing: -0.2,
  },
  yourRankSecondary: {
    ...Typography.bodySm,
    color: Colors.textSecondary,
    fontSize: 11,
    marginTop: 2,
  },
  listContent: {
    paddingHorizontal: 14,
    paddingTop: 4,
    paddingBottom: 24,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderRadius: 12,
    backgroundColor: Colors.surface,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.06)',
  },
  rowRank: {
    fontFamily: 'Inter_800ExtraBold',
    color: Colors.gold,
    fontSize: 14,
    width: 32,
  },
  rowMid: {
    flex: 1,
  },
  rowName: {
    ...Typography.body,
    fontSize: 13,
    color: Colors.textPrimary,
    fontFamily: 'Inter_800ExtraBold',
  },
  rowMission: {
    ...Typography.bodySm,
    fontSize: 10,
    color: Colors.textSecondary,
    marginTop: 1,
  },
  rowRight: {
    alignItems: 'flex-end',
  },
  rowXp: {
    ...Typography.body,
    fontSize: 12,
    fontFamily: 'Inter_800ExtraBold',
    color: Colors.terracotta,
  },
  rowBurst: {
    ...Typography.bodySm,
    fontSize: 9,
    color: Colors.textSecondary,
    marginTop: 1,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  separator: {
    height: 6,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    padding: 24,
  },
  centerText: {
    ...Typography.bodySm,
    color: Colors.textSecondary,
    fontSize: 12,
  },
  errorTitle: {
    ...Typography.body,
    color: Colors.textPrimary,
    fontSize: 14,
    fontFamily: 'Inter_800ExtraBold',
  },
  emptyText: {
    ...Typography.body,
    color: Colors.textSecondary,
    fontSize: 13,
    textAlign: 'center',
  },
  retryBtn: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 10,
    backgroundColor: Colors.terracotta,
    marginTop: 8,
  },
  retryTxt: {
    color: Colors.textPrimary,
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 12,
    letterSpacing: 0.4,
    textTransform: 'uppercase',
  },
});
