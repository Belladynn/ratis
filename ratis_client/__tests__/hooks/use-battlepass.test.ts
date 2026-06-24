// __tests__/hooks/use-battlepass.test.ts
//
// `useBattlepass` adapts the backend `GET /gamification/battlepass` response
// (season + cab_earned_season + milestones) into the legacy `BattlepassState`
// shape consumed by the card / header. These tests assert the mapping rules.

import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import {
  adaptBattlepassResponse,
  useBattlepass,
} from '@/hooks/use-battlepass';
import { rewardsClient } from '@/services/rewards-client';

jest.mock('@/services/rewards-client', () => ({
  rewardsClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('adaptBattlepassResponse', () => {
  it('returns null when there is no active season', () => {
    const adapted = adaptBattlepassResponse({ season: null });
    expect(adapted).toBeNull();
  });

  it('maps season.name + cab_earned_season into the legacy shape', () => {
    const adapted = adaptBattlepassResponse({
      season: { id: 's1', name: 'Saison 1', ends_at: '2026-09-01T00:00:00Z' },
      cab_earned_season: 480,
      milestones: [
        {
          id: 'm1', milestone_number: 1, cab_required: 200,
          reward_type: 'cab', reward_value: 100,
          subscriber_only: false, status: 'claimed',
        },
        {
          id: 'm2', milestone_number: 2, cab_required: 500,
          reward_type: 'gift_card', reward_value: 5,
          subscriber_only: false, status: 'unlocked',
        },
        {
          id: 'm3', milestone_number: 3, cab_required: 1000,
          reward_type: 'cab', reward_value: 500,
          subscriber_only: false, status: 'locked',
        },
      ],
    });
    expect(adapted).not.toBeNull();
    expect(adapted?.season_name).toBe('Saison 1');
    // 1 milestone claimed → current_level = 1
    expect(adapted?.current_level).toBe(1);
    expect(adapted?.xp_current).toBe(480);
    // Next non-claimed milestone is m2 (cab_required = 500)
    expect(adapted?.xp_next_level).toBe(500);
    expect(adapted?.next_reward_label).toBe('Carte cadeau 5€');
    expect(adapted?.next_reward_type).toBe('skin');
  });

  it('falls back to last milestone when every milestone is claimed', () => {
    const adapted = adaptBattlepassResponse({
      season: { id: 's1', name: 'Saison 1', ends_at: '2026-09-01T00:00:00Z' },
      cab_earned_season: 9999,
      milestones: [
        {
          id: 'm1', milestone_number: 1, cab_required: 200,
          reward_type: 'cab', reward_value: 100,
          subscriber_only: false, status: 'claimed',
        },
        {
          id: 'm2', milestone_number: 2, cab_required: 500,
          reward_type: 'cab', reward_value: 250,
          subscriber_only: false, status: 'claimed',
        },
      ],
    });
    expect(adapted?.current_level).toBe(2);
    expect(adapted?.xp_next_level).toBe(500);
    expect(adapted?.next_reward_label).toBe('+250 CAB');
    expect(adapted?.next_reward_type).toBe('cab');
  });

  it('returns sensible defaults when milestones is empty', () => {
    const adapted = adaptBattlepassResponse({
      season: { id: 's1', name: 'Solo', ends_at: '2026-09-01T00:00:00Z' },
      cab_earned_season: 0,
      milestones: [],
    });
    expect(adapted?.current_level).toBe(0);
    expect(adapted?.xp_current).toBe(0);
    expect(adapted?.xp_next_level).toBe(1);
    expect(adapted?.next_reward_label).toBe('');
    expect(adapted?.next_reward_type).toBeNull();
  });

  // Bug 5 (PO ticket 2026-05-12 wave 2) — explicit fresh-user case.
  // Adapter must return `current_level: 0` (never negative) so the
  // BattlepassCard renders « Niv. 0 » in the header rather than « -1 ».
  it('fresh user (no claimed milestones) → current_level=0, not -1', () => {
    const adapted = adaptBattlepassResponse({
      season: { id: 's1', name: 'Fresh', ends_at: '2026-09-01T00:00:00Z' },
      cab_earned_season: 0,
      milestones: [
        {
          id: 'm1', milestone_number: 1, cab_required: 200,
          reward_type: 'cab', reward_value: 100,
          subscriber_only: false, status: 'unlocked',
        },
        {
          id: 'm2', milestone_number: 2, cab_required: 500,
          reward_type: 'cab', reward_value: 250,
          subscriber_only: false, status: 'locked',
        },
      ],
    });
    expect(adapted?.current_level).toBe(0);
    expect(adapted?.current_level).not.toBe(-1);
    expect(adapted?.xp_next_level).toBe(200);
  });
});

describe('useBattlepass', () => {
  it('queries /gamification/battlepass and adapts the payload', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      season: { id: 's1', name: 'S1', ends_at: '2026-09-01T00:00:00Z' },
      cab_earned_season: 100,
      milestones: [
        {
          id: 'm1', milestone_number: 1, cab_required: 200,
          reward_type: 'cab', reward_value: 50,
          subscriber_only: false, status: 'locked',
        },
      ],
    });
    const { result } = renderHook(() => useBattlepass(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(rewardsClient.get).toHaveBeenCalledWith('/gamification/battlepass');
    expect(result.current.data?.season_name).toBe('S1');
    expect(result.current.data?.current_level).toBe(0);
    expect(result.current.data?.xp_next_level).toBe(200);
  });

  it('returns null when the backend has no active season', async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({ season: null });
    const { result } = renderHook(() => useBattlepass(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it('returns error state on API failure', async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useBattlepass(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
