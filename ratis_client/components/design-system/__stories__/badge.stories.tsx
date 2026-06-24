/**
 * Storybook stories for <Badge />.
 *
 * Coverage : 4 rarities × 3 sizes grid, icon variant, shine toggle.
 */

import React from 'react';
import { Text, View } from 'react-native';

import { Badge, type BadgeProps } from '../badge';
import { Colors, Spacing, Typography } from '../../../constants/theme';

type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
};
type BadgeStory = {
  args?: Partial<BadgeProps>;
  render?: () => React.ReactElement;
};

const meta: StoryMeta<BadgeProps> = {
  title: 'Design System/Badge',
  component: Badge,
  args: { rarity: 'rare', label: 'Rare' },
};
export default meta;

export const Common: BadgeStory = { args: { rarity: 'common', label: 'Common' } };
export const Rare: BadgeStory = { args: { rarity: 'rare', label: 'Rare' } };
export const Epic: BadgeStory = { args: { rarity: 'epic', label: 'Epic' } };
export const Legendary: BadgeStory = {
  args: { rarity: 'legendary', label: 'Legendary' },
};

export const WithIcon: BadgeStory = {
  args: {
    rarity: 'legendary',
    label: 'First scan',
    icon: <Text>🏆</Text>,
  },
};

export const SmallEpic: BadgeStory = {
  args: { rarity: 'epic', label: 'Small', size: 'sm' },
};

export const LargeLegendary: BadgeStory = {
  args: { rarity: 'legendary', label: 'XL', size: 'lg' },
};

export const NoShine: BadgeStory = {
  args: { rarity: 'legendary', label: 'No shine', shine: false },
};

/**
 * 4 rarities × 3 sizes — full QA matrix.
 */
export const Gallery: BadgeStory = {
  render: () => (
    <View style={{ gap: Spacing.lg }}>
      {(['common', 'rare', 'epic', 'legendary'] as const).map((rarity) => (
        <View key={rarity} style={{ gap: Spacing.sm }}>
          <Text style={[Typography.label, { color: Colors.textSecondary }]}>
            {rarity.toUpperCase()}
          </Text>
          <View style={{ flexDirection: 'row', gap: Spacing.sm }}>
            <Badge rarity={rarity} label="SM" size="sm" />
            <Badge rarity={rarity} label="MD" size="md" />
            <Badge rarity={rarity} label="LG" size="lg" />
          </View>
        </View>
      ))}
    </View>
  ),
};
