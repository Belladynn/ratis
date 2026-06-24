// ratis_client/components/dashboard/missions-block.tsx
//
// "MissionsBlock" — port of `Ratis_handoff/lib/ratis-real-v4.jsx`
// `function MissionsBlock` (lines 451-496) and `function MissionsCard`
// (lines 286-336).
//
// Anatomy :
//   - Wrapper holding two stacked cards :
//       a. Weekly card  — violet border / accent
//       b. Daily card   — orange border / accent
//   - A single chest SVG image (`assets/images/cabecoin-chest.svg`) overlays
//     both cards (mirrored horizontally), opacity 0.32. The JSX hides the
//     chest's middle band by an opaque strip — we keep the same trick, with
//     a `Colors.bg`-colored separator between the two cards. The chest is
//     positioned via absolute coordinates and is purely decorative
//     (`pointerEvents="none"`).
//
// Each card row : checkbox + label (line-through if done) + small gold
// "+{xp_reward}" button. Pressing the button claims the mission via the
// `onClaim` prop (the dashboard composition wires it to `useClaimMission`).
//
// V1 limitations / follow-ups :
//   - Chest SVG is large (~714KB). It loads via `react-native-svg-transformer`
//     (configured in `metro.config.js`) so it ships as a React component.
//     If startup perf becomes a concern, swap to a PNG export at the asset
//     level — no API change needed.
//   - The "Tu as terminé !" empty state is hidden : we keep the JSX behavior
//     of always showing 4 rows (slice). Empty list → renders 0 rows, header
//     still shows the `0/0` count.

import React from 'react';
import {
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';

import { Colors } from '@/constants/theme';
import type { DailyMission } from '@/types/gamification';
import { MissionRowBB } from '@/components/dashboard/mission-row-bb';
import ChestSvg from '@/assets/images/cabecoin-chest.svg';

export type MissionsBlockProps = {
  weekly: readonly DailyMission[];
  daily: readonly DailyMission[];
  onClaim?: (missionId: string) => void;
  /** Buffer + Burst — opens the confirm modal upstream. Optional. */
  onBufferPress?: (mission: DailyMission) => void;
  /** Buffer + Burst — claim accumulated Burst paliers. Optional. */
  onBurstClaim?: (missionId: string) => void;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

const VARIANTS = {
  weekly: {
    titleColor: Colors.violet,
    icon: '★',
    title: 'Missions de la semaine',
    borderColor: 'rgba(139,92,246,0.35)',
    shadowColor: 'rgba(60,30,120,0.55)',
    checkBg: '#8B5CF6',
  },
  daily: {
    titleColor: Colors.orange,
    icon: '📅',
    title: 'Missions du jour',
    borderColor: 'rgba(251,146,60,0.35)',
    shadowColor: 'rgba(60,30,10,0.55)',
    checkBg: '#FB923C',
  },
} as const;

type Variant = keyof typeof VARIANTS;

/** A row counts as "done" when status is no longer in-progress. */
function isRowDone(m: DailyMission): boolean {
  return m.status === 'completed' || m.status === 'claimed';
}

function MissionsCard({
  variant,
  missions,
  onClaim,
  onBufferPress,
  onBurstClaim,
  testID,
}: {
  variant: Variant;
  missions: readonly DailyMission[];
  onClaim?: (id: string) => void;
  onBufferPress?: (m: DailyMission) => void;
  onBurstClaim?: (id: string) => void;
  testID?: string;
}) {
  const v = VARIANTS[variant];
  const completed = missions.filter(isRowDone).length;
  const displayed = missions.slice(0, 4);
  return (
    <View
      testID={testID}
      style={[
        styles.card,
        {
          borderColor: v.borderColor,
          shadowColor: v.shadowColor,
        },
      ]}
    >
      <View style={styles.cardHeader}>
        <Text style={styles.cardHeaderIcon}>{v.icon}</Text>
        <Text style={[styles.cardHeaderTitle, { color: v.titleColor }]}>
          {v.title}
        </Text>
        <Text style={[styles.cardHeaderCount, { color: v.titleColor }]}>
          {completed}/{missions.length}
        </Text>
      </View>
      {displayed.map((m) => (
        <MissionRowBB
          key={m.id}
          mission={m}
          variant={variant}
          onClaim={onClaim}
          onBufferPress={onBufferPress}
          onBurstClaim={onBurstClaim}
        />
      ))}
    </View>
  );
}

export function MissionsBlock({
  weekly,
  daily,
  onClaim,
  onBufferPress,
  onBurstClaim,
  testID = 'missions-block',
  style,
}: MissionsBlockProps) {
  return (
    <View testID={testID} style={[styles.root, style]}>
      <MissionsCard
        testID={`${testID}-weekly`}
        variant="weekly"
        missions={weekly}
        onClaim={onClaim}
        onBufferPress={onBufferPress}
        onBurstClaim={onBurstClaim}
      />
      {/* Opaque separator strip — sits ABOVE the chest to clip it cleanly. */}
      <View style={styles.separator} />
      <MissionsCard
        testID={`${testID}-daily`}
        variant="daily"
        missions={daily}
        onClaim={onClaim}
        onBufferPress={onBufferPress}
        onBurstClaim={onBurstClaim}
      />
      {/* Single chest image overlaying both cards (mirrored, opacity 0.32).
          Bug 3 (PO ticket 2026-05-12) — the SVG previously used
          `preserveAspectRatio="xMidYMid meet"` (= CSS `object-fit: contain`)
          which made the chest letterbox vertically inside the wrapper at
          our narrow card widths, leaving it « floating » in the middle
          band between the two cards. We now use `xMidYMid slice`
          (= `object-fit: cover`) so the chest is guaranteed to fill the
          full height of the wrapper top-to-bottom, crossing both cards
          continuously. The 10px opaque separator above clips the middle
          chest band cleanly. */}
      <View
        pointerEvents="none"
        testID={`${testID}-chest`}
        style={styles.chestWrapper}
      >
        <ChestSvg
          width="100%"
          height="100%"
          preserveAspectRatio="xMidYMid slice"
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    position: 'relative',
    width: '100%',
    flexDirection: 'column',
  },
  card: {
    borderRadius: 20,
    backgroundColor: Colors.surface,
    borderWidth: 1.5,
    padding: 16,
    overflow: 'hidden',
    position: 'relative',
    zIndex: 1,
    // 3D shadow — single hard layer.
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 1,
    shadowRadius: 0,
    elevation: 5,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 4,
  },
  cardHeaderIcon: {
    fontSize: 13,
  },
  cardHeaderTitle: {
    flex: 1,
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 11,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
  cardHeaderCount: {
    fontFamily: 'Inter_800ExtraBold',
    fontSize: 11,
  },
  separator: {
    // Opaque strip at body color (matches `Colors.bg`) — sits above the chest
    // to clip its mid-section between the two cards.
    height: 10,
    backgroundColor: '#1a242c',
    position: 'relative',
    zIndex: 3,
  },
  chestWrapper: {
    position: 'absolute',
    right: '18%',
    top: 0,
    bottom: 0,
    width: '40%',
    transform: [{ scaleX: -1 }],
    opacity: 0.32,
    zIndex: 2,
  },
});

export default MissionsBlock;
