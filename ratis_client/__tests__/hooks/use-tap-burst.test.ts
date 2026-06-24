// ratis_client/__tests__/hooks/use-tap-burst.test.ts
//
// Achievements V1.1 — generic "tap N times within Tms" detector tests.
//
// The hook is the input layer for the V1.1 Konami secret (5 taps on the
// profil avatar in 1.5s). Tests cover :
//   - Threshold reached → onComplete fires + counter resets
//   - Below threshold → onComplete does NOT fire
//   - Inactivity beyond `windowMs` → counter resets
//   - Counter resets after firing (so back-to-back bursts both fire)
//   - reset() cancels in-flight burst
//   - Cleanup on unmount cancels pending reset timer

import { act, renderHook } from '@testing-library/react-native';

import { useTapBurst } from '@/hooks/use-tap-burst';

describe('useTapBurst', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('fires onComplete after exactly `threshold` register() calls in window', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() =>
      useTapBurst({ threshold: 5, windowMs: 1500, onComplete }),
    );
    act(() => {
      for (let i = 0; i < 5; i++) result.current.register();
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('does NOT fire when threshold not reached', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() =>
      useTapBurst({ threshold: 5, windowMs: 1500, onComplete }),
    );
    act(() => {
      for (let i = 0; i < 4; i++) result.current.register();
    });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('resets the counter when inactivity exceeds windowMs', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() =>
      useTapBurst({ threshold: 5, windowMs: 1500, onComplete }),
    );
    act(() => {
      result.current.register();
      result.current.register();
      result.current.register();
    });
    // Wait past the window — the burst should reset.
    act(() => {
      jest.advanceTimersByTime(1500);
    });
    // Now 4 more taps must NOT fire (we'd need 5 fresh taps).
    act(() => {
      for (let i = 0; i < 4; i++) result.current.register();
    });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('extends the window on each new tap (sliding inactivity)', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() =>
      useTapBurst({ threshold: 5, windowMs: 1500, onComplete }),
    );
    act(() => {
      result.current.register();
    });
    act(() => {
      jest.advanceTimersByTime(1000); // < window — still alive
    });
    act(() => {
      result.current.register();
    });
    act(() => {
      jest.advanceTimersByTime(1000); // < window since previous tap
    });
    act(() => {
      result.current.register();
      result.current.register();
      result.current.register();
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('fires twice across two distinct bursts', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() =>
      useTapBurst({ threshold: 3, windowMs: 1500, onComplete }),
    );
    act(() => {
      for (let i = 0; i < 3; i++) result.current.register();
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
    act(() => {
      for (let i = 0; i < 3; i++) result.current.register();
    });
    expect(onComplete).toHaveBeenCalledTimes(2);
  });

  it('reset() cancels an in-flight burst', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() =>
      useTapBurst({ threshold: 5, windowMs: 1500, onComplete }),
    );
    act(() => {
      result.current.register();
      result.current.register();
      result.current.register();
    });
    act(() => {
      result.current.reset();
    });
    act(() => {
      // Need 5 fresh taps to fire — only 2 here.
      result.current.register();
      result.current.register();
    });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('does nothing when threshold is 0 or negative (defensive)', () => {
    const onComplete = jest.fn();
    const { result } = renderHook(() =>
      useTapBurst({ threshold: 0, windowMs: 1500, onComplete }),
    );
    act(() => {
      for (let i = 0; i < 10; i++) result.current.register();
    });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('survives a throwing onComplete (does not poison hook state)', () => {
    const onComplete = jest.fn(() => {
      throw new Error('listener boom');
    });
    const { result } = renderHook(() =>
      useTapBurst({ threshold: 2, windowMs: 1500, onComplete }),
    );
    act(() => {
      result.current.register();
      result.current.register();
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
    // Hook still works for the next burst.
    act(() => {
      result.current.register();
      result.current.register();
    });
    expect(onComplete).toHaveBeenCalledTimes(2);
  });

  it('cleans up its pending timer on unmount', () => {
    const onComplete = jest.fn();
    const { result, unmount } = renderHook(() =>
      useTapBurst({ threshold: 5, windowMs: 1500, onComplete }),
    );
    act(() => {
      result.current.register();
    });
    unmount();
    // Advancing timers post-unmount must not throw / call onComplete.
    expect(() => {
      jest.advanceTimersByTime(2000);
    }).not.toThrow();
    expect(onComplete).not.toHaveBeenCalled();
  });
});
