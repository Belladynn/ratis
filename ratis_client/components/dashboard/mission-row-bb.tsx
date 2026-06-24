// ratis_client/components/dashboard/mission-row-bb.tsx
//
// Buffer + Burst (refonte 2026-05-09) — extended mission row.
//
// Renders the canonical mission row + the Buffer/Burst overlay :
//   - Buffer button (visible only on daily/is_boostable/buffer_count<3/!burst_locked/pending)
//   - "Buffer × N" indicator (when buffer_count > 0)
//   - Burst overlay progress bar (when current_count >= target_count)
//   - "Burst claim" button (when un-claimed paliers are available)
//
// All Buffer/Burst fields on `DailyMission` are optional — the backend
// payload may not yet expose them in `GET /gamification/missions`. When
// they are absent, the component renders only the canonical row (= same
// shape as the legacy MissionRow). This keeps the component forward-
// compatible with the backend payload extension that surfaces them.
//
// The component is a pure renderer : it receives `onBufferPress` /
// `onBurstClaim` callbacks from the parent which owns the mutations.

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { Colors, Typography } from '@/constants/theme';
import { Button, ProgressBar } from '@/components/design-system';
import type { DailyMission } from '@/types/gamification';
import { getMissionLabel } from '@/types/gamification';

export type MissionRowBBVariant = 'weekly' | 'daily';

export type MissionRowBBProps = {
  mission: DailyMission;
  variant: MissionRowBBVariant;
  /** Triggered by the gold "claim" button (= the legacy claim CTA). */
  onClaim?: (id: string) => void;
  /** Triggered by the "Buffer" button (opens the confirm modal upstream). */
  onBufferPress?: (mission: DailyMission) => void;
  /** Triggered by the "Burst claim" button on the overlay bar. */
  onBurstClaim?: (id: string) => void;
  /**
   * Bug 2 (wave 4 — PO ticket 2026-05-12) — toggles the Buffer button
   * rendering. Defaults to ``false`` : the button is hidden for the
   * alpha while the Buffer feature is deferred. Code paths
   * (``onBufferPress``, the badge, ``canBuffer``) are preserved so
   * flipping this prop to ``true`` re-enables the feature without
   * touching the row internals. The dashboard composition leaves the
   * default in place — V1.5 will lift it.
   */
  bufferEnabled?: boolean;
  testID?: string;
};

const VARIANTS = {
  weekly: { checkBg: '#8B5CF6' },
  daily: { checkBg: '#FB923C' },
} as const;

/** Buffer is offered only when ALL conditions hold. */
function canBuffer(m: DailyMission): boolean {
  return (
    m.frequency === 'daily' &&
    m.is_boostable === true &&
    (m.buffer_count ?? 0) < 3 &&
    m.burst_locked !== true &&
    (m.status === 'pending' || m.status === 'active')
  );
}

/** Burst overlay is shown once the user has crossed the target. */
function hasBurstOverlay(m: DailyMission): boolean {
  if (m.target_count <= 0) return false;
  return m.current_count >= m.target_count;
}

/** Number of unclaimed Burst paliers available right now. */
function unclaimedBurstPaliers(m: DailyMission): number {
  if (!hasBurstOverlay(m)) return 0;
  // palier N is at target × 2^N (N=1,2,3,…). Math.log2 rounds down.
  const ratio = m.current_count / m.target_count;
  if (ratio < 2) return 0;
  const reached = Math.floor(Math.log2(ratio));
  const claimed = m.burst_count ?? 0;
  return Math.max(0, reached - claimed);
}

/**
 * Burst overlay bar value (0..1) — progress towards the NEXT palier.
 *
 * Once the user has reached palier N (= current >= target × 2^N) the bar
 * shows progress in the [target × 2^N, target × 2^(N+1)] range.
 */
function burstOverlayValue(m: DailyMission): number {
  if (!hasBurstOverlay(m)) return 0;
  const ratio = m.current_count / m.target_count;
  if (ratio < 2) return 0;
  const reached = Math.floor(Math.log2(ratio));
  const lower = Math.pow(2, reached);
  const upper = Math.pow(2, reached + 1);
  const clamped = Math.min(Math.max((ratio - lower) / (upper - lower), 0), 1);
  return clamped;
}

export function MissionRowBB({
  mission,
  variant,
  onClaim,
  onBufferPress,
  onBurstClaim,
  bufferEnabled = false,
  testID,
}: MissionRowBBProps) {
  const { t } = useTranslation();
  const v = VARIANTS[variant];
  const done = mission.status === 'completed' || mission.status === 'claimed';
  const claimable = mission.status === 'completed';
  // Bug 2 (PO ticket 2026-05-12) — incomplete missions (pending/active) get
  // a muted/greyed visual treatment so the user instantly distinguishes
  // « still to do » from « ready to claim ». Done rows keep their full-
  // colour rendering (line-through label + filled checkbox).
  const incomplete =
    mission.status === 'pending' || mission.status === 'active';
  const bufferCount = mission.buffer_count ?? 0;
  // Bug 2 (wave 4 — PO ticket 2026-05-12) — Buffer button hidden for
  // alpha. ``bufferEnabled`` defaults to ``false`` so all call-sites
  // (dashboard composition included) opt out automatically. The
  // ``canBuffer`` evaluation is preserved so flipping the prop or the
  // backend payload future-proofs the re-enable.
  const showBuffer = bufferEnabled && canBuffer(mission);
  const showBufferBadge = bufferCount > 0;
  const showBurst = hasBurstOverlay(mission);
  const burstUnclaimed = unclaimedBurstPaliers(mission);
  const rowTestID = testID ?? `mission-row-${mission.id}`;

  return (
    <View
      testID={rowTestID}
      style={[styles.container, incomplete && styles.containerIncomplete]}
    >
      <View style={styles.row}>
        <View
          style={[
            styles.checkbox,
            {
              borderColor: done ? v.checkBg : 'rgba(255,255,255,0.12)',
              backgroundColor: done ? v.checkBg : 'transparent',
            },
          ]}
        >
          {done ? <Text style={styles.checkboxMark}>✓</Text> : null}
        </View>
        <View style={styles.labelCol}>
          <Text
            style={[styles.label, done && styles.labelDone]}
            numberOfLines={1}
          >
            {getMissionLabel(mission.action_type)}
          </Text>
          {showBufferBadge ? (
            <Text
              testID={`${rowTestID}-buffer-badge`}
              style={styles.bufferBadge}
            >
              {t('gamification.buffer.badge', { count: bufferCount })}
            </Text>
          ) : null}
        </View>
        {/* Bug 1 (wave 4 — PO ticket 2026-05-12) — claim button label
            simplified to ``+N`` (no "CAB" suffix). The gold styling +
            the header CAB-balance pill already make the currency
            unambiguous ; trimming the suffix unifies column widths
            across rows (`+5` vs `+500` no longer race). The
            `cab_reward_a11y` key still produces the full « Réclamer :
            N CAB » VoiceOver label so accessibility doesn't lose the
            unit. ``styles.claimBtn`` is locked at 80 px wide
            (fits three digits) regardless of label length. */}
        {showBuffer ? (
          <Button
            testID={`${rowTestID}-buffer-btn`}
            size="sm"
            variant="secondary"
            label={t('gamification.buffer.cta')}
            onPress={() => onBufferPress?.(mission)}
            style={styles.bufferBtn}
            accessibilityLabel={t('gamification.buffer.cta_a11y')}
          />
        ) : null}
        <Button
          testID={`mission-claim-${mission.id}`}
          size="sm"
          variant="gold"
          label={`+${mission.cab_reward}`}
          disabled={!claimable}
          onPress={claimable ? () => onClaim?.(mission.id) : undefined}
          style={styles.claimBtn}
          accessibilityLabel={t('gamification.mission.cab_reward_a11y', {
            count: mission.cab_reward,
          })}
        />
      </View>
      {showBurst ? (
        <View testID={`${rowTestID}-burst-overlay`} style={styles.burstWrap}>
          <View style={styles.burstHeader}>
            <Text style={styles.burstLabel}>
              {t('gamification.burst.overlay_title', {
                level: mission.burst_count ?? 0,
              })}
            </Text>
            {burstUnclaimed > 0 ? (
              <Button
                testID={`${rowTestID}-burst-claim-btn`}
                size="sm"
                variant="primary"
                label={t('gamification.burst.claim_cta', {
                  count: burstUnclaimed,
                })}
                onPress={() => onBurstClaim?.(mission.id)}
                style={styles.burstClaimBtn}
              />
            ) : null}
          </View>
          <ProgressBar
            testID={`${rowTestID}-burst-progress`}
            value={burstOverlayValue(mission)}
            variant="terracotta"
            height={6}
            shimmer={false}
          />
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: '100%',
  },
  containerIncomplete: {
    // Bug 2 (PO ticket 2026-05-12) — incomplete (pending/active) rows are
    // dimmed so the user spots claimable ones immediately. 0.55 keeps the
    // row readable while signalling « not yet ».
    opacity: 0.55,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginTop: 6,
  },
  checkbox: {
    width: 16,
    height: 16,
    borderRadius: 8,
    borderWidth: 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkboxMark: {
    color: Colors.textPrimary,
    fontSize: 9,
    fontWeight: '700',
    lineHeight: 11,
  },
  labelCol: {
    flex: 1,
    flexDirection: 'column',
  },
  label: {
    ...Typography.body,
    fontSize: 12,
    color: Colors.textPrimary,
    lineHeight: 16,
  },
  labelDone: {
    color: 'rgba(255,255,255,0.55)',
    textDecorationLine: 'line-through',
    opacity: 0.85,
  },
  bufferBadge: {
    ...Typography.bodySm,
    fontSize: 10,
    color: Colors.terracotta,
    fontFamily: 'Inter_800ExtraBold',
    letterSpacing: 0.4,
    textTransform: 'uppercase',
    marginTop: 2,
  },
  bufferBtn: {
    minWidth: 56,
  },
  claimBtn: {
    // Bug 1 wave 4 — the label was trimmed from ``+{N} CAB`` back to
    // ``+{N}`` (the gold styling already conveys the unit). Fixed
    // 80 px on BOTH ``width`` and ``minWidth`` so all claim buttons line
    // up vertically across rows regardless of the reward magnitude
    // (`+5`, `+50`, `+500`). 80 px fits 3 digits + the leading ``+``.
    width: 80,
    minWidth: 80,
  },
  burstWrap: {
    marginTop: 8,
    marginLeft: 24,
    marginRight: 4,
    paddingTop: 4,
    paddingBottom: 2,
    gap: 4,
  },
  burstHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  burstLabel: {
    ...Typography.bodySm,
    fontSize: 10,
    color: Colors.terracotta,
    fontFamily: 'Inter_800ExtraBold',
    letterSpacing: 0.4,
    textTransform: 'uppercase',
    flex: 1,
  },
  burstClaimBtn: {
    minWidth: 64,
  },
});

export default MissionRowBB;
