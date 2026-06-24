/**
 * Single import surface for the design system primitives.
 *
 *   import { Button, Card, ProgressBar, Badge, CoinBurst } from
 *     '@/components/design-system';
 *
 * New primitives must be added here so consumers don't need to know the
 * filename — refactors stay internal.
 */

export {
  Button,
  type ButtonProps,
  type ButtonVariant,
  type ButtonSize,
} from './button';
export {
  Card,
  type CardProps,
  type CardVariant,
  type CardAccentColor,
} from './card';
export {
  ProgressBar,
  type ProgressBarProps,
  type ProgressBarVariant,
} from './progress-bar';
export {
  Badge,
  type BadgeProps,
  type BadgeRarity,
  type BadgeSize,
} from './badge';
export { CoinBurst, type CoinBurstProps } from './coin-burst';
export {
  SegmentedTabs,
  type SegmentedTabsProps,
  type SegmentedTab,
} from './segmented-tabs';
export { Modal, type ModalProps } from './modal';
export { Toast, type ToastProps } from './toast';
export { Avatar, type AvatarProps, type AvatarSize } from './avatar';
export { Stepper, type StepperProps } from './stepper';
export { CheckBurst, type CheckBurstProps } from './check-burst';
