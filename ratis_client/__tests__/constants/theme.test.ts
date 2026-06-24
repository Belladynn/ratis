import {
  Colors,
  Design,
  Fonts,
  LegacyColors,
  Radii,
  Rarity,
  RewardTiers,
  RingColors,
  Shadows,
  Spacing,
  Typography,
} from '@/constants/theme';

// Backward-compat: legacy tokens still consumed by un-refactored screens.
// They will be migrated as part of PR3/PR4. Keep these tests so a future
// regression on the legacy export is caught before it reaches prod.
describe('Legacy Design tokens (backward-compat)', () => {
  it('exports color tokens', () => {
    expect(Design.colors.bg).toBe('#1a2e38');
    expect(Design.colors.paper).toBe('#D4A574');
    expect(Design.colors.gold).toBe('#FFB800');
    expect(Design.colors.teal).toBe('#00D9B5');
    expect(Design.colors.green).toBe('#10B981');
  });

  it('exports spacing tokens', () => {
    expect(Design.spacing.xs).toBe(4);
    expect(Design.spacing.sm).toBe(8);
    expect(Design.spacing.md).toBe(16);
    expect(Design.spacing.lg).toBe(24);
  });

  it('exports radius tokens', () => {
    expect(Design.radius.card).toBe(0);
    expect(Design.radius.badge).toBe(6);
    expect(Design.radius.icon).toBe(8);
    expect(Design.radius.pill).toBe(999);
  });

  it('exports legacy light/dark color scheme used by useThemeColor', () => {
    expect(LegacyColors.light.text).toBeDefined();
    expect(LegacyColors.dark.text).toBeDefined();
    expect(LegacyColors.light.background).toBe('#fff');
    expect(LegacyColors.dark.background).toBe('#151718');
    expect(LegacyColors.light.icon).toBeDefined();
    expect(LegacyColors.dark.icon).toBeDefined();
  });

  it('exports legacy Fonts platform map', () => {
    expect(Fonts).toBeDefined();
    // Platform.select returns the matching arch's value.
    expect(typeof Fonts!.sans).toBe('string');
    expect(typeof Fonts!.mono).toBe('string');
  });
});

describe('Pivot palette (Duolingo/Clash 2026-05-03)', () => {
  describe('Colors — backgrounds', () => {
    it('bg is #1a2428 (cardinal rule — never anything else)', () => {
      expect(Colors.bg).toBe('#1a2428');
    });
    it('surface and overlay match ARCH', () => {
      expect(Colors.surface).toBe('#27293A');
      expect(Colors.overlay).toBe('#0F1419');
    });
  });

  describe('Colors — semantic roles', () => {
    it('terracotta is the action color #DA7756', () => {
      expect(Colors.terracotta).toBe('#DA7756');
      expect(Colors.terracottaHi).toBe('#E8896A');
      expect(Colors.terracottaLo).toBe('#A8562E');
      expect(Colors.terracottaSh).toBe('#6B3218');
    });
    it('gold for claim / rewards #FFB800', () => {
      expect(Colors.gold).toBe('#FFB800');
      expect(Colors.goldHi).toBe('#FFE066');
      expect(Colors.goldLo).toBe('#B47800');
      expect(Colors.goldSh).toBe('#7E5300');
    });
    it('jarPink for économies #FF6B9D', () => {
      expect(Colors.jarPink).toBe('#FF6B9D');
      expect(Colors.jarPinkHi).toBe('#FF8FB3');
      expect(Colors.jarPinkBg1).toBe('#2A1A1A');
      expect(Colors.jarPinkBg2).toBe('#1F1212');
    });
  });

  describe('Colors — secondary accents', () => {
    it('exposes violet/orange/cyan/amber/coral with text variants', () => {
      expect(Colors.violet).toBe('#A78BFA');
      expect(Colors.violetText).toBe('#C4B5FD');
      expect(Colors.orange).toBe('#FF6B35');
      expect(Colors.orangeText).toBe('#FFB89D');
      expect(Colors.cyan).toBe('#0EA5E9');
      expect(Colors.cyanText).toBe('#67E8F9');
      expect(Colors.amber).toBe('#F59E0B');
      expect(Colors.amberText).toBe('#FCD34D');
      expect(Colors.coral).toBe('#EF4444');
      expect(Colors.coralText).toBe('#FCA5A5');
    });
  });

  describe('Colors — text', () => {
    it('text tokens', () => {
      expect(Colors.textPrimary).toBe('#FFFFFF');
      expect(Colors.textSecondary).toBe('rgba(255,255,255,0.45)');
      expect(Colors.textTertiary).toBe('rgba(255,255,255,0.30)');
      expect(Colors.textMuted).toBe('rgba(255,255,255,0.40)');
    });
  });

  describe('RingColors — 10 rings cycle (cyan → purple)', () => {
    it('has exactly 10 entries', () => {
      expect(RingColors).toHaveLength(10);
    });
    it('starts cyan, ends purple', () => {
      expect(RingColors[0]).toBe('#22D3EE');
      expect(RingColors[9]).toBe('#A855F7');
    });
    it('is exported readonly (as const tuple)', () => {
      // Compile-time `as const` cannot be asserted at runtime, but ensure
      // each element is a non-empty hex string.
      RingColors.forEach((c) => {
        expect(c).toMatch(/^#[0-9A-Fa-f]{6}$/);
      });
    });
  });

  describe('RewardTiers + Rarity', () => {
    it('5 tiers bronze→diamond with [hi, lo] gradient pairs', () => {
      expect(RewardTiers.bronze).toEqual(['#CD7F32', '#8B4513']);
      expect(RewardTiers.silver).toEqual(['#C0C0C0', '#808080']);
      expect(RewardTiers.gold).toEqual(['#FFD700', '#FFA500']);
      expect(RewardTiers.platinum).toEqual(['#E5E4E2', '#B0C4DE']);
      expect(RewardTiers.diamond).toEqual(['#FF6B9D', '#A855F7']);
    });
    it('rarity ladder common→legendary', () => {
      expect(Rarity.common).toBe('rgba(255,255,255,0.40)');
      expect(Rarity.rare).toBe('#22D3EE');
      expect(Rarity.epic).toBe('#A855F7');
      expect(Rarity.legendary).toBe('#FFB800');
    });
  });

  describe('Spacing / Radii', () => {
    it('Spacing on 4px base', () => {
      expect(Spacing.xs).toBe(4);
      expect(Spacing.sm).toBe(8);
      expect(Spacing.md).toBe(12);
      expect(Spacing.lg).toBe(16);
      expect(Spacing.xl).toBe(24);
      expect(Spacing.xxl).toBe(32);
    });
    it('Radii match ARCH (icon=10, badge=8, btn=14, card=20)', () => {
      expect(Radii.icon).toBe(10);
      expect(Radii.badge).toBe(8);
      expect(Radii.btn).toBe(14);
      expect(Radii.btnSm).toBe(12);
      expect(Radii.card).toBe(20);
      expect(Radii.modal).toBe(24);
    });
  });

  describe('Typography — Inter weights, ARCH-mapped sizes', () => {
    it('label 9px / 800 / +0.8 letter-spacing', () => {
      expect(Typography.label.fontSize).toBe(9);
      expect(Typography.label.fontFamily).toBe('Inter_800ExtraBold');
      expect(Typography.label.letterSpacing).toBe(0.8);
    });
    it('cardTitle 15px / 900', () => {
      expect(Typography.cardTitle.fontSize).toBe(15);
      expect(Typography.cardTitle.fontFamily).toBe('Inter_900Black');
      expect(Typography.cardTitle.letterSpacing).toBe(-0.3);
    });
    it('hero 22px / 900 / -0.6', () => {
      expect(Typography.hero.fontSize).toBe(22);
      expect(Typography.hero.letterSpacing).toBe(-0.6);
    });
    it('metric 28px / 900 / -1.2', () => {
      expect(Typography.metric.fontSize).toBe(28);
      expect(Typography.metric.letterSpacing).toBe(-1.2);
    });
    it('body uses Inter_400Regular', () => {
      expect(Typography.body.fontFamily).toBe('Inter_400Regular');
    });
  });

  describe('Shadows — 3D Clash Royale', () => {
    it('card hard shadow uses positive Y offset (drop shadow downward)', () => {
      expect(Shadows.card.hard.shadowOffset.height).toBeGreaterThan(0);
      expect(Shadows.card.hard.shadowRadius).toBe(0);
      expect(Shadows.card.hard.elevation).toBe(5);
    });
    it('buttonPrimary shadow uses terracotta dark tone', () => {
      expect(Shadows.buttonPrimary.hard.shadowColor).toBe('#6B3218');
      expect(Shadows.buttonPrimary.hard.shadowOffset.height).toBe(4);
    });
    it('buttonClaim shadow uses gold dark tone', () => {
      expect(Shadows.buttonClaim.hard.shadowColor).toBe('#7E5300');
    });
  });
});
