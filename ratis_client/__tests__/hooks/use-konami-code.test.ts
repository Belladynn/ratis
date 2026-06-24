// ratis_client/__tests__/hooks/use-konami-code.test.ts
//
// Achievements V1 — Konami code hook contract tests (PR 8/8).
import { act, renderHook } from '@testing-library/react-native';

import {
  KONAMI_SEQUENCE,
  useKonamiCode,
} from '@/hooks/use-konami-code';

describe('KONAMI_SEQUENCE', () => {
  it('is the canonical 10-step ↑↑↓↓←→←→BA pattern', () => {
    expect(KONAMI_SEQUENCE).toEqual([
      'up',
      'up',
      'down',
      'down',
      'left',
      'right',
      'left',
      'right',
      'b',
      'a',
    ]);
  });
});

describe('useKonamiCode', () => {
  it('exposes a single push() handler that triggers on the full sequence', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() => useKonamiCode(onComplete));
    for (const key of KONAMI_SEQUENCE) {
      act(() => {
        result.current.push(key);
      });
    }
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('does not fire when the partial sequence is wrong', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() => useKonamiCode(onComplete));
    act(() => {
      result.current.push('up');
      result.current.push('down'); // wrong — should be up
      result.current.push('down');
      result.current.push('down');
      result.current.push('left');
      result.current.push('right');
      result.current.push('left');
      result.current.push('right');
      result.current.push('b');
      result.current.push('a');
    });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('resets and re-locks when a wrong key breaks the sequence', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() => useKonamiCode(onComplete));
    // First two correct
    act(() => {
      result.current.push('up');
      result.current.push('up');
    });
    // Wrong → reset
    act(() => {
      result.current.push('a');
    });
    // Now play full sequence — must succeed
    act(() => {
      for (const key of KONAMI_SEQUENCE) {
        result.current.push(key);
      }
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('handles a wrong key that itself starts the sequence (greedy restart)', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() => useKonamiCode(onComplete));
    act(() => {
      result.current.push('a'); // wrong - reset, but 'a' itself isn't 'up'
      result.current.push('up'); // start fresh
      for (const key of KONAMI_SEQUENCE.slice(1)) {
        result.current.push(key);
      }
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('reset() clears progress', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() => useKonamiCode(onComplete));
    act(() => {
      result.current.push('up');
      result.current.push('up');
      result.current.push('down');
    });
    act(() => {
      result.current.reset();
    });
    // Continue the sequence as if from scratch — should not complete since
    // we still need 10 from start.
    act(() => {
      for (const key of KONAMI_SEQUENCE.slice(3)) {
        result.current.push(key);
      }
    });
    expect(onComplete).not.toHaveBeenCalled();
  });
});
