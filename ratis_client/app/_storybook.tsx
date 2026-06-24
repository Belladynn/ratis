/**
 * Storybook RN entry route — design system QA.
 *
 * Accessible via :
 *
 *   1. Variable d'env : `EXPO_PUBLIC_STORYBOOK_ENABLED=true` au start
 *      (mais cette branche-ci ne court-circuite PAS le RootLayout, c'est
 *      juste un toggle d'auto-redirect vers cette route).
 *   2. Direct nav en dev : push `/_storybook` depuis le dev menu /
 *      shake → "Open URL" → `exp://localhost/_storybook`.
 *
 * En **production**, cette route est inaccessible — `__DEV__ === false`
 * fait afficher un écran "Not available" et l'app ne propose aucun lien
 * pour y arriver. Ne jamais ajouter de bouton vers cette page dans l'UI
 * principale (sinon un user prod pourrait tomber dessus).
 */

import React from 'react';
import { Text, View } from 'react-native';

import { Colors, Typography } from '@/constants/theme';

// Lazy-import : on ne charge le Storybook bundle qu'en dev pour ne pas
// le shipper en prod. La condition `__DEV__` est statiquement éliminée
// par Metro via dead-code-elimination → en prod, `Storybook` reste
// `null` et le bundle Storybook n'est pas inclus.
let StorybookUI: React.ComponentType | null = null;
if (__DEV__) {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  StorybookUI = require('../.storybook').default;
}

export default function StorybookRoute() {
  if (!__DEV__ || !StorybookUI) {
    return (
      <View
        style={{
          flex: 1,
          backgroundColor: Colors.bg,
          alignItems: 'center',
          justifyContent: 'center',
          padding: 24,
        }}
      >
        <Text
          style={{
            ...Typography.cardTitle,
            color: Colors.textPrimary,
            textAlign: 'center',
          }}
        >
          Storybook is dev-only.
        </Text>
        <Text
          style={{
            ...Typography.bodySm,
            color: Colors.textSecondary,
            marginTop: 12,
            textAlign: 'center',
          }}
        >
          Set EXPO_PUBLIC_STORYBOOK_ENABLED=true in development to use it.
        </Text>
      </View>
    );
  }

  const SB = StorybookUI;
  return <SB />;
}
