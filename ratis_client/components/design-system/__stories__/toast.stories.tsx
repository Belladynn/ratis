/**
 * Storybook stories for <Toast />.
 *
 * Coverage : a static "visible" state for layout inspection, and an
 * interactive auto-dismiss simulation reproducing the real lifecycle.
 */

import React, { useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { Toast, type ToastProps } from '../toast';
import { Button } from '../button';
import { Colors, Spacing } from '../../../constants/theme';

type StoryMeta<P> = {
  title: string;
  component: React.ComponentType<P>;
  args?: Partial<P>;
};
type ToastStory = {
  args?: Partial<ToastProps>;
  render?: () => React.ReactElement;
};

const meta: StoryMeta<ToastProps> = {
  title: 'Design System/Toast',
  component: Toast,
  args: {
    visible: true,
    message: 'Mission claimed · +200 cab',
    onDismiss: () => undefined,
  },
};
export default meta;

export const Visible: ToastStory = {
  args: {
    visible: true,
    message: 'Mission claimed · +200 cab',
    onDismiss: () => undefined,
  },
};

function AutoDismissDemo() {
  const [visible, setVisible] = useState(false);
  return (
    <View style={styles.host}>
      <Button
        variant="primary"
        size="md"
        label="Trigger toast"
        onPress={() => setVisible(true)}
      />
      <Toast
        visible={visible}
        message="Mission claimed · +200 cab"
        onDismiss={() => setVisible(false)}
        duration={1800}
      />
    </View>
  );
}

export const AutoDismissSimulation: ToastStory = {
  render: () => <AutoDismissDemo />,
};

const styles = StyleSheet.create({
  host: {
    flex: 1,
    minHeight: 320,
    backgroundColor: Colors.bg,
    alignItems: 'center',
    justifyContent: 'center',
    padding: Spacing.lg,
  },
});
