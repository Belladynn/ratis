/**
 * Storybook stories for <CheckBurst />.
 *
 * Coverage : interactive trigger that re-arms on press, plus a colored
 * variant matching a Liste category.
 */

import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { CheckBurst, type CheckBurstProps } from '../check-burst';
import { Colors, Spacing, Typography } from '../../../constants/theme';

type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
};
type CheckBurstStory = {
  args?: Partial<CheckBurstProps>;
  render?: () => React.ReactElement;
};

const meta: StoryMeta<CheckBurstProps> = {
  title: 'Design System/CheckBurst',
  component: CheckBurst,
};
export default meta;

function Demo({ color = '#5EE5C2' }: { color?: string }) {
  const [seq, setSeq] = useState(0);
  const [play, setPlay] = useState(false);

  return (
    <View style={styles.host}>
      <Pressable
        accessibilityRole="button"
        onPress={() => {
          setPlay(true);
          setSeq((s) => s + 1);
        }}
        style={styles.dot}
      >
        <View style={styles.dotCenter} />
        {/* Re-mount on each tap so the effect re-runs cleanly. */}
        <CheckBurst
          key={seq}
          play={play}
          color={color}
          originX={12}
          originY={12}
        />
      </Pressable>
      <Text style={[Typography.bodySm, { color: Colors.textSecondary }]}>
        Tap the green square to trigger
      </Text>
    </View>
  );
}

export const OnTapTrigger: CheckBurstStory = {
  render: () => <Demo />,
};

export const GoldVariant: CheckBurstStory = {
  render: () => <Demo color={Colors.gold} />,
};

const styles = StyleSheet.create({
  host: {
    flex: 1,
    minHeight: 240,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Colors.bg,
    gap: Spacing.lg,
  },
  dot: {
    width: 24,
    height: 24,
    borderRadius: 8,
    backgroundColor: 'rgba(94,229,194,0.45)',
    borderWidth: 1.5,
    borderColor: 'rgba(94,229,194,0.7)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  dotCenter: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: 'rgba(0,0,0,0.4)',
  },
});
