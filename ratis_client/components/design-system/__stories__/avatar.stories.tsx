/**
 * Storybook stories for <Avatar />.
 *
 * Coverage : all 3 sizes (sm/md/lg), a gold-ring variant matching the
 * `Profil.png` hero, and a custom-gradient variant.
 */

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Avatar, type AvatarProps } from '../avatar';
import { Colors, Spacing, Typography } from '../../../constants/theme';

type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
};
type AvatarStory = {
  args?: Partial<AvatarProps>;
  render?: () => React.ReactElement;
};

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.lg,
    padding: Spacing.lg,
  },
  emojiSm: { fontSize: 18 },
  emoji: { fontSize: 26 },
  emojiLg: { fontSize: 44 },
});

const meta: StoryMeta<AvatarProps> = {
  title: 'Design System/Avatar',
  component: Avatar,
  args: { size: 'md', children: <Text style={styles.emoji}>🐀</Text> },
};
export default meta;

export const Small: AvatarStory = {
  args: { size: 'sm', children: <Text style={styles.emojiSm}>🐀</Text> },
};
export const Medium: AvatarStory = {
  args: { size: 'md', children: <Text style={styles.emoji}>🐀</Text> },
};
export const Large: AvatarStory = {
  args: { size: 'lg', children: <Text style={styles.emojiLg}>🐀</Text> },
};

export const WithGoldRing: AvatarStory = {
  args: {
    size: 'lg',
    ringColor: Colors.gold,
    children: <Text style={styles.emojiLg}>🐀</Text>,
  },
};

export const AllSizes: AvatarStory = {
  render: () => (
    <View style={styles.row}>
      <Avatar size="sm">
        <Text style={styles.emojiSm}>🐀</Text>
      </Avatar>
      <Avatar size="md">
        <Text style={styles.emoji}>🐀</Text>
      </Avatar>
      <Avatar size="lg" ringColor={Colors.gold}>
        <Text style={styles.emojiLg}>🐀</Text>
      </Avatar>
    </View>
  ),
};

export const InitialsCustomGradient: AvatarStory = {
  render: () => (
    <Avatar size="lg" gradientColors={[Colors.violet, '#5B21B6']}>
      <Text style={[Typography.cardTitle, { color: Colors.textPrimary }]}>
        ML
      </Text>
    </Avatar>
  ),
};
