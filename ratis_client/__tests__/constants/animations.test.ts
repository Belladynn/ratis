import { Durations, EasingPresets } from '@/constants/animations';

describe('Animation Durations', () => {
  it('exposes the standard tiers', () => {
    expect(Durations.instant).toBe(100);
    expect(Durations.fast).toBe(200);
    expect(Durations.normal).toBe(300);
    expect(Durations.slow).toBe(500);
  });

  it('caps page transitions at 300ms (ARCH rule)', () => {
    // "Hard rule : pas de transition > 300ms hors célébration."
    expect(Durations.normal).toBeLessThanOrEqual(300);
  });

  describe('loop durations (ARCH § Animations)', () => {
    it('jackPulse 2s', () => {
      expect(Durations.loop.jackPulse).toBe(2000);
    });
    it('roiHaloPulse 2.4s', () => {
      expect(Durations.loop.roiHaloPulse).toBe(2400);
    });
    it('roiLightSpin 3.2s', () => {
      expect(Durations.loop.roiLightSpin).toBe(3200);
    });
    it('roiFossilBlink 1.6s (per fossil, stagger 0.4s)', () => {
      expect(Durations.loop.roiFossilBlink).toBe(1600);
    });
    it('jarHaloPulse 2.4s (parity with roiHaloPulse)', () => {
      expect(Durations.loop.jarHaloPulse).toBe(2400);
    });
    it('jarRayspin 14s (slow rotation)', () => {
      expect(Durations.loop.jarRayspin).toBe(14000);
    });
    it('jarSparkle ~5.5s average (per instance, randomized)', () => {
      expect(Durations.loop.jarSparkle).toBe(5500);
    });
    it('jarCoinFall ~3.5s average (per instance)', () => {
      expect(Durations.loop.jarCoinFall).toBe(3500);
    });
  });
});

describe('EasingPresets', () => {
  it('exposes named easing keys for the canonical presets', () => {
    expect(EasingPresets.out).toBe('cubicOut');
    expect(EasingPresets.inOut).toBe('cubicInOut');
    expect(EasingPresets.linear).toBe('linear');
  });

  it('bouncy preset uses the slideUp bezier from design pattern v2', () => {
    expect(EasingPresets.bouncy).toEqual([0.2, 0.9, 0.3, 1.2]);
  });
});
