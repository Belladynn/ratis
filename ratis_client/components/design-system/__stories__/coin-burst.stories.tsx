/**
 * Storybook stories for <CoinBurst />.
 *
 * Coverage : a Trigger-Burst story with a button that re-arms the burst (so
 * QA can replay the animation), a many-coins variant for visual stress test,
 * and a static "always visible" variant for layout inspection.
 */

import React, { useState } from 'react';
import { View, Text } from 'react-native';

import { CoinBurst, type CoinBurstProps } from '../coin-burst';
import { Button } from '../button';
import { Colors, Spacing, Typography } from '../../../constants/theme';

type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
};
type CoinStory = {
  args?: Partial<CoinBurstProps>;
  render?: () => React.ReactElement;
};

const meta: StoryMeta<CoinBurstProps> = {
  title: 'Design System/CoinBurst',
  component: CoinBurst,
  args: { visible: true, count: 8 },
};
export default meta;

export const StaticBurst: CoinStory = { args: { visible: true, count: 8 } };

export const ManyCoins: CoinStory = { args: { visible: true, count: 16 } };

export const Hidden: CoinStory = { args: { visible: false } };

/**
 * Interactive story — tap the button to re-trigger the burst.
 *
 * Wrapped in a real React component (`InteractiveBurst`) so React's
 * rules-of-hooks accept the `useState` calls — the alternative would be a
 * `render: () => { useState(...) }` arrow which violates the linter rule
 * (function name `render` doesn't start with `use` and isn't capitalized).
 */
function InteractiveBurst() {
  const [seq, setSeq] = useState(0);
  const [visible, setVisible] = useState(false);

  return (
    <View style={{ alignItems: 'center', gap: Spacing.xl }}>
      <Text style={[Typography.cardTitle, { color: Colors.gold }]}>
        +120 CAB
      </Text>
      <View style={{ height: 120, justifyContent: 'center' }}>
        <CoinBurst
          visible={visible}
          count={10}
          origin={{ x: 0, y: 0 }}
          onComplete={() => setVisible(false)}
          // Force remount on each press to re-arm the burst.
          key={seq}
        />
      </View>
      <Button
        variant="gold"
        size="sm"
        label="Trigger burst"
        onPress={() => {
          setVisible(true);
          setSeq((s) => s + 1);
        }}
      />
    </View>
  );
}

export const TriggerOnTap: CoinStory = {
  render: () => <InteractiveBurst />,
};
