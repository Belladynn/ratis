/**
 * Storybook stories for <Card />.
 *
 * Coverage : standard layout, the 6 supported accent colors, tappable card,
 * and a card with corner glow.
 */

import React from 'react';
import { Text, View } from 'react-native';

import { Card, type CardProps } from '../card';
import { Colors, Spacing, Typography } from '../../../constants/theme';

type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
};
type CardStory = { args?: Partial<CardProps>; render?: () => React.ReactElement };

const meta: StoryMeta<CardProps> = {
  title: 'Design System/Card',
  component: Card,
  args: {
    children: (
      <Text style={[Typography.cardTitle, { color: Colors.textPrimary }]}>
        Carte standard
      </Text>
    ),
  },
};
export default meta;

export const Standard: CardStory = {
  args: {},
};

export const AccentJarPink: CardStory = {
  args: {
    variant: 'accent',
    accentColor: 'jarPink',
    children: (
      <Text style={[Typography.cardTitle, { color: Colors.jarPink }]}>
        Économies — 22,40 €
      </Text>
    ),
  },
};

export const AccentGold: CardStory = {
  args: {
    variant: 'accent',
    accentColor: 'gold',
    children: (
      <Text style={[Typography.cardTitle, { color: Colors.gold }]}>
        +120 CAB à récupérer
      </Text>
    ),
  },
};

export const AccentViolet: CardStory = {
  args: {
    variant: 'accent',
    accentColor: 'violet',
    children: (
      <Text style={[Typography.cardTitle, { color: Colors.violetText }]}>
        Mission hebdo
      </Text>
    ),
  },
};

export const AccentCyan: CardStory = {
  args: {
    variant: 'accent',
    accentColor: 'cyan',
    children: (
      <Text style={[Typography.cardTitle, { color: Colors.cyanText }]}>
        Battle pass — Saison 1
      </Text>
    ),
  },
};

export const Tappable: CardStory = {
  args: {
    onPress: () => {},
    children: (
      <Text style={[Typography.cardTitle, { color: Colors.textPrimary }]}>
        Tap me
      </Text>
    ),
  },
};

export const WithCornerGlow: CardStory = {
  args: {
    variant: 'accent',
    accentColor: 'jarPink',
    cornerGlow: true,
    children: (
      <Text style={[Typography.cardTitle, { color: Colors.jarPink }]}>
        Avec corner glow
      </Text>
    ),
  },
};

/**
 * Composite gallery — every accent color stacked vertically for QA.
 */
export const Gallery: CardStory = {
  render: () => (
    <View style={{ gap: Spacing.md }}>
      {(
        ['jarPink', 'gold', 'terracotta', 'violet', 'orange', 'cyan'] as const
      ).map((c) => (
        <Card key={c} variant="accent" accentColor={c}>
          <Text style={[Typography.cardTitle, { color: Colors.textPrimary }]}>
            Accent — {c}
          </Text>
        </Card>
      ))}
    </View>
  ),
};
