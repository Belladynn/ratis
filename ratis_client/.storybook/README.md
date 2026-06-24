# Storybook RN — Ratis design system

Storybook RN 8.x — QA visuelle des composants du design system avant merge.
Référence : `ARCH_design_system.md` § Stack tech.

## Objectif

Stack pivot 2026-05-03 (Duolingo / Clash Royale) → composants chunky 3D
avec gradients + ombres dures + animations Reanimated. Le rendu réel
diverge fortement du wireframe Figma — on a besoin d'un atelier visuel
**dans l'app Expo** (pas un site web séparé) pour itérer sur device.

## Lancer Storybook

```bash
cd ratis_client
npm run storybook:generate   # collecte les *.stories.tsx en storybook.requires.ts
EXPO_PUBLIC_STORYBOOK_ENABLED=true npm run start
```

L'app boot remplace son écran racine par la Storybook UI (cf
`app/_storybook.tsx`). Pour revenir à l'app normale, retire la variable
d'env et redémarre Metro.

## Ajouter une story (PR3+)

```tsx
// components/design-system/__stories__/button.stories.tsx
import type { Meta, StoryObj } from '@storybook/react-native';
import { Button } from '../button';

const meta: Meta<typeof Button> = {
  title: 'design-system/Button',
  component: Button,
  args: { variant: 'primary', children: 'Action' },
};
export default meta;

type Story = StoryObj<typeof Button>;
export const Primary: Story = {};
export const Gold: Story = { args: { variant: 'gold' } };
export const Danger: Story = { args: { variant: 'danger' } };
```

Puis `npm run storybook:generate` pour rafraîchir le registre.

## Statut PR2

PR2 = **scaffold uniquement**. Aucune story n'existe encore — Storybook
charge un registre vide qui rend un écran "No stories found" par défaut.
PR3 ajoutera les stories des primitives (`Button`, `Card`, `ProgressBar`,
`Badge`, `CoinBurst`).

## Pourquoi RN-Storybook plutôt que Storybook web ?

Le design pattern v2 dépend de :

- `react-native-reanimated@4` worklets (ne tournent pas en JSDOM)
- `expo-blur` (BlurView natif, pas de polyfill web)
- `react-native-svg` (rendu différent web vs native)

Un Storybook web aurait des divergences de rendu — pas la peine de QA
quelque chose qu'on ne va pas shipper. L'atelier RN tourne dans Expo
Go / dev client, donc on QA exactement ce qui sera en prod.
