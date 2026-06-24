/**
 * Storybook stories for <SegmentedTabs />.
 */

import React, { useState } from 'react';
import { View, Text } from 'react-native';

import { SegmentedTabs, type SegmentedTabsProps, type SegmentedTab } from '../segmented-tabs';
import { Colors, Spacing, Typography } from '../../../constants/theme';

type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
};
type Story = { args?: Partial<SegmentedTabsProps>; render?: () => React.ReactElement };

const TWO: SegmentedTab[] = [
  { id: 'daily', label: 'Quotidien' },
  { id: 'weekly', label: 'Hebdo' },
];
const THREE: SegmentedTab[] = [
  { id: 'all', label: 'Toutes' },
  { id: 'active', label: 'En cours' },
  { id: 'done', label: 'Terminées' },
];
const FOUR: SegmentedTab[] = [
  { id: 'a', label: 'Auj.' },
  { id: 'b', label: 'Sem.' },
  { id: 'c', label: 'Mois' },
  { id: 'd', label: 'Année' },
];

const meta: StoryMeta<SegmentedTabsProps> = {
  title: 'Design System/SegmentedTabs',
  component: SegmentedTabs,
  args: { tabs: TWO, activeId: 'daily', onChange: () => {} },
};
export default meta;

function Controlled({ tabs, initial }: { tabs: SegmentedTab[]; initial: string }) {
  const [active, setActive] = useState(initial);
  return (
    <View style={{ padding: Spacing.lg, backgroundColor: Colors.bg, gap: Spacing.md }}>
      <SegmentedTabs tabs={tabs} activeId={active} onChange={setActive} />
      <Text style={[Typography.body as object, { color: Colors.textSecondary }]}>
        active : {active}
      </Text>
    </View>
  );
}

export const TwoTabs: Story = { render: () => <Controlled tabs={TWO} initial="daily" /> };
export const ThreeTabs: Story = { render: () => <Controlled tabs={THREE} initial="active" /> };
export const FourTabs: Story = { render: () => <Controlled tabs={FOUR} initial="b" /> };

export const Gallery: Story = {
  render: () => (
    <View style={{ padding: Spacing.lg, backgroundColor: Colors.bg, gap: Spacing.lg }}>
      <Controlled tabs={TWO} initial="daily" />
      <Controlled tabs={THREE} initial="active" />
      <Controlled tabs={FOUR} initial="b" />
    </View>
  ),
};
