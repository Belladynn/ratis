/**
 * Tests for the design system <CoinBurst /> primitive.
 *
 * Coverage : visible toggle (renders coins or nothing), default count, custom
 * count honoured, onComplete fires once at the end of the burst window.
 *
 * Reanimated 4 timing relies on the worklet runtime ; jest-expo provides a
 * mock that resolves animations synchronously enough for our setTimeout-based
 * onComplete contract — but to remain hermetic we drive the clock via fake
 * timers and assert on `setTimeout`-scheduled callback execution.
 */

import React from 'react';
import { render, act } from '@testing-library/react-native';

import { CoinBurst } from '@/components/design-system/coin-burst';

describe('<CoinBurst />', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('renders nothing when visible=false', () => {
    const { queryByTestId } = render(
      <CoinBurst visible={false} testID="cb" />,
    );
    expect(queryByTestId('cb')).toBeNull();
  });

  it('renders the default count of coins when visible=true', () => {
    const { getAllByTestId } = render(<CoinBurst visible testID="cb" />);
    // Default count = 8 per ARCH § Composants — CoinBurst.
    expect(getAllByTestId(/^cb-coin-/)).toHaveLength(8);
  });

  it('renders the requested coin count', () => {
    const { getAllByTestId } = render(
      <CoinBurst visible count={12} testID="cb" />,
    );
    expect(getAllByTestId(/^cb-coin-/)).toHaveLength(12);
  });

  it('calls onComplete after the burst duration', () => {
    const onComplete = jest.fn();
    render(<CoinBurst visible onComplete={onComplete} testID="cb" />);
    expect(onComplete).not.toHaveBeenCalled();
    // Advance past the burst window (coin duration + max stagger across
    // 8 coins, ~1567ms — schedule the callback via setTimeout so timers
    // drive it deterministically).
    act(() => {
      jest.advanceTimersByTime(2200);
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('does not call onComplete when not visible', () => {
    const onComplete = jest.fn();
    render(
      <CoinBurst visible={false} onComplete={onComplete} testID="cb" />,
    );
    act(() => {
      jest.advanceTimersByTime(2200);
    });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('exposes the wrapper testID', () => {
    const { getByTestId } = render(<CoinBurst visible testID="cb" />);
    expect(getByTestId('cb')).toBeTruthy();
  });
});
