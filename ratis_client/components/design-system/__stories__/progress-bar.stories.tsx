/**
 * Storybook stories for <ProgressBar />.
 *
 * Coverage : 4 variants, static value samples (0/25/50/75/100%), label
 * toggle, shimmer toggle.
 */

import React from 'react';
import { View, Text } from 'react-native';

import { ProgressBar, type ProgressBarProps } from '../progress-bar';
import { Colors, Spacing, Typography } from '../../../constants/theme';

type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
};
type ProgressStory = {
  args?: Partial<ProgressBarProps>;
  render?: () => React.ReactElement;
};

const meta: StoryMeta<ProgressBarProps> = {
  title: 'Design System/ProgressBar',
  component: ProgressBar,
  args: { value: 0.5, variant: 'gold' },
};
export default meta;

export const Gold50: ProgressStory = { args: { value: 0.5, variant: 'gold' } };
export const JarPink75: ProgressStory = {
  args: { value: 0.75, variant: 'jarPink' },
};
export const TerracottaFull: ProgressStory = {
  args: { value: 1, variant: 'terracotta' },
};
export const CyanQuarter: ProgressStory = {
  args: { value: 0.25, variant: 'cyan' },
};

export const WithLabel: ProgressStory = {
  args: { value: 0.6, variant: 'gold', showLabel: true },
};

export const NoShimmer: ProgressStory = {
  args: { value: 0.5, variant: 'jarPink', shimmer: false },
};

/**
 * Composite gallery — every variant at 4 progress steps for QA.
 */
export const Gallery: ProgressStory = {
  render: () => (
    <View style={{ gap: Spacing.lg }}>
      {(['gold', 'jarPink', 'terracotta', 'cyan'] as const).map((v) => (
        <View key={v} style={{ gap: Spacing.sm }}>
          <Text style={[Typography.label, { color: Colors.textSecondary }]}>
            {v.toUpperCase()}
          </Text>
          {[0, 0.25, 0.5, 0.75, 1].map((p) => (
            <ProgressBar key={`${v}-${p}`} value={p} variant={v} showLabel />
          ))}
        </View>
      ))}
    </View>
  ),
};
