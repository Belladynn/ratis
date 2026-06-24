import { StyleSheet, Text, type TextProps } from 'react-native';

import { useThemeColor } from '@/hooks/use-theme-color';

export type ThemedTextProps = TextProps & {
  lightColor?: string;
  darkColor?: string;
  type?: 'default' | 'title' | 'defaultSemiBold' | 'subtitle' | 'link';
};

export function ThemedText({
  style,
  lightColor,
  darkColor,
  type = 'default',
  ...rest
}: ThemedTextProps) {
  const color = useThemeColor({ light: lightColor, dark: darkColor }, 'text');

  return (
    <Text
      style={[
        { color },
        type === 'default' ? styles.default : undefined,
        type === 'title' ? styles.title : undefined,
        type === 'defaultSemiBold' ? styles.defaultSemiBold : undefined,
        type === 'subtitle' ? styles.subtitle : undefined,
        type === 'link' ? styles.link : undefined,
        style,
      ]}
      {...rest}
    />
  );
}

// PR2 design system : on bascule la fontFamily par défaut sur Inter — chargé
// au lifespan racine (`useDesignSystemFonts`). Si le bundle font n'est pas
// encore résolu, RN tombe gracefully sur la fonte système, donc safe.
// `fontWeight` reste utilisé pour les variantes legacy ; le mapping vers
// les weights Inter sera fait par les nouveaux composants design-system.
const styles = StyleSheet.create({
  default: {
    fontFamily: 'Inter_400Regular',
    fontSize: 16,
    lineHeight: 24,
  },
  defaultSemiBold: {
    fontFamily: 'Inter_600SemiBold',
    fontSize: 16,
    lineHeight: 24,
    fontWeight: '600',
  },
  title: {
    fontFamily: 'Inter_900Black',
    fontSize: 32,
    fontWeight: 'bold',
    lineHeight: 32,
  },
  subtitle: {
    fontFamily: 'Inter_700Bold',
    fontSize: 20,
    fontWeight: 'bold',
  },
  link: {
    fontFamily: 'Inter_400Regular',
    lineHeight: 30,
    fontSize: 16,
    color: '#0a7ea4',
  },
});
