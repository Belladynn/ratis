// ratis_client/components/dashboard/missions-modal.tsx
//
// "MissionsModal" — port of `Ratis_handoff/lib/ratis-real-v4.jsx`
// `function MissionsModal` (lines 805-877).
//
// Bottom-sheet popup variant of the dashboard `MissionsBlock`. Same data
// (weekly + daily) but laid out without the chest SVG overlay : each card
// sits in its own `Colors.surface` rounded surface with a coloured 1px
// border (violet for weekly, orange for daily) per JSX iso.
//
// We reuse the design-system `<Modal />` primitive (`components/design-system/
// modal.tsx`) — it already implements the bottom-sheet animation contract
// from this exact JSX (backdrop fadeIn 200ms + slideUp bouncy bezier 260ms).
// The `<Modal />` also renders the drag handle, close button, eyebrow + title.
//
// Why not extract the existing `MissionRow` / inner `MissionsCard` from
// `missions-block.tsx` ? The dashboard variant interleaves a chest SVG
// between the two cards via an opaque separator strip — that mechanism is
// dashboard-only. The modal needs two **independent** card surfaces (each
// with its own border colour). Inlining a tiny mission-row helper keeps
// the modal self-contained and avoids over-fitting the existing component.
//
// Token derogation : numeric values come straight from JSX iso source
// (cf `chunk-3-followups.md` § 10).
//
// Reanimated cleanup : this file does NOT introduce continuous animations.
// The `<Modal />` primitive owns its own `withTiming` lifecycle (no
// `withRepeat`). No `cancelAnimation` cleanup needed here.

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Colors } from '@/constants/theme';
import { Modal } from '@/components/design-system';
import { MissionRowBB } from '@/components/dashboard/mission-row-bb';
import type { DailyMission } from '@/types/gamification';

export type MissionsModalProps = {
  open: boolean;
  onClose: () => void;
  weekly: readonly DailyMission[];
  daily: readonly DailyMission[];
  onClaim?: (missionId: string) => void;
  /** Buffer + Burst — opens the confirm modal upstream. Optional. */
  onBufferPress?: (mission: DailyMission) => void;
  /** Buffer + Burst — claim accumulated Burst paliers. Optional. */
  onBurstClaim?: (missionId: string) => void;
  testID?: string;
};

const VARIANTS = {
  weekly: {
    titleColor: Colors.violet,
    icon: '★',
    title: 'Missions de la semaine',
    borderColor: 'rgba(167,139,250,0.25)',
    checkBg: '#8B5CF6',
  },
  daily: {
    titleColor: Colors.orange,
    icon: '📅',
    title: 'Missions du jour',
    borderColor: 'rgba(255,107,53,0.25)',
    checkBg: '#FB923C',
  },
} as const;

type Variant = keyof typeof VARIANTS;

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
  testID: string;
}) {
  const v = VARIANTS[variant];
  const completed = missions.filter(isRowDone).length;
  const displayed = missions.slice(0, 4);
  return (
    <View
      testID={testID}
      style={[styles.card, { borderColor: v.borderColor }]}
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
          testID={`missions-modal-row-${m.id}`}
        />
      ))}
    </View>
  );
}

export function MissionsModal({
  open,
  onClose,
  weekly,
  daily,
  onClaim,
  onBufferPress,
  onBurstClaim,
  testID = 'missions-modal',
}: MissionsModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Tes missions"
      title="Missions actives"
      testID={testID}
    >
      <MissionsCard
        testID={`${testID}-weekly`}
        variant="weekly"
        missions={weekly}
        onClaim={onClaim}
        onBufferPress={onBufferPress}
        onBurstClaim={onBurstClaim}
      />
      <MissionsCard
        testID={`${testID}-daily`}
        variant="daily"
        missions={daily}
        onClaim={onClaim}
        onBufferPress={onBufferPress}
        onBurstClaim={onBurstClaim}
      />
    </Modal>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: Colors.surface,
    borderRadius: 18,
    borderWidth: 1,
    padding: 14,
    // inset 0 1px 0 rgba(255,255,255,0.06) approximation — matches JSX
    shadowColor: 'rgba(255,255,255,0.06)',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 1,
    shadowRadius: 0,
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
});

export default MissionsModal;
