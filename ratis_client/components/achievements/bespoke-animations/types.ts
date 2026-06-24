// ratis_client/components/achievements/bespoke-animations/types.ts
//
// Shared prop contract for every bespoke unlock cinematic. Kept in a
// dedicated file so individual bespoke components can import it without
// pulling the registry (avoids a circular dependency between registry.ts
// and the components it lists).

import type { AchievementUnlockedPayload } from '@/types/achievements';

export interface BespokeUnlockProps {
  payload: AchievementUnlockedPayload;
  onDismiss: () => void;
  testID?: string;
}
