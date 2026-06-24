/**
 * Storybook stories for <Modal /> (bottom sheet).
 *
 * Coverage : open + close interaction (closeable backdrop / × button), and a
 * scrollable variant with long content to verify overflow and `82%` max
 * height.
 */

import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Modal, type ModalProps } from '../modal';
import { Button } from '../button';
import { Colors, Spacing, Typography } from '../../../constants/theme';

type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
};
type ModalStory = {
  args?: Partial<ModalProps>;
  render?: () => React.ReactElement;
};

const meta: StoryMeta<ModalProps> = {
  title: 'Design System/Modal',
  component: Modal,
};
export default meta;

function OpenAndCloseable() {
  const [open, setOpen] = useState(false);
  return (
    <View style={styles.host}>
      <Button
        variant="primary"
        size="md"
        label="Open modal"
        onPress={() => setOpen(true)}
      />
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Tes missions"
        title="Missions actives"
        scrollable={false}
      >
        <View style={styles.card}>
          <Text style={[Typography.cardTitle, { color: Colors.textPrimary }]}>
            Mission de la semaine
          </Text>
          <Text style={[Typography.bodySm, { color: Colors.textSecondary }]}>
            Tape le backdrop ou la croix pour fermer.
          </Text>
        </View>
      </Modal>
    </View>
  );
}

function ScrollableContent() {
  const [open, setOpen] = useState(true);
  return (
    <View style={styles.host}>
      <Pressable
        accessibilityRole="button"
        onPress={() => setOpen(true)}
        style={styles.reopen}
      >
        <Text style={{ color: Colors.textPrimary }}>Re-open</Text>
      </Pressable>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Démo"
        title="Long content"
      >
        {Array.from({ length: 14 }).map((_, i) => (
          <View key={i} style={styles.card}>
            <Text
              style={[Typography.itemTitle, { color: Colors.textPrimary }]}
            >
              Row #{i + 1}
            </Text>
            <Text style={[Typography.bodySm, { color: Colors.textSecondary }]}>
              Lorem ipsum dolor sit amet, consectetur adipiscing elit.
            </Text>
          </View>
        ))}
      </Modal>
    </View>
  );
}

export const OpenCloseable: ModalStory = {
  render: () => <OpenAndCloseable />,
};
export const ScrollableLongContent: ModalStory = {
  render: () => <ScrollableContent />,
};

const styles = StyleSheet.create({
  host: {
    flex: 1,
    minHeight: 400,
    backgroundColor: Colors.bg,
    alignItems: 'center',
    justifyContent: 'center',
    padding: Spacing.lg,
  },
  reopen: {
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.sm,
    backgroundColor: Colors.surface,
    borderRadius: 12,
  },
  card: {
    backgroundColor: Colors.surface,
    borderRadius: 14,
    padding: Spacing.md,
    gap: Spacing.xs,
  },
});
