// ratis_client/components/ui/location-permission-banner.tsx
//
// Inline banner prompting the user to enable foreground geolocation.
// Used as a non-blocking replacement for Alert.alert across scan/liste/produit
// screens: subtle, dismissible, always-in-flow.
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

export type LocationPermissionContext = 'scan' | 'liste' | 'produit';

interface LocationPermissionBannerProps {
  context: LocationPermissionContext;
  onRequestPermission: () => void;
  onDismiss?: () => void;
}

export function LocationPermissionBanner({
  context,
  onRequestPermission,
  onDismiss,
}: LocationPermissionBannerProps) {
  const { t } = useTranslation();

  return (
    <View style={styles.banner} testID="location-permission-banner">
      <Text style={styles.icon}>📍</Text>
      <View style={styles.body}>
        <Text style={styles.title}>{t('common.location_permission.title')}</Text>
        <Text style={styles.desc}>
          {t(`common.location_permission.description.${context}`)}
        </Text>
      </View>
      <View style={styles.actions}>
        <Pressable
          onPress={onRequestPermission}
          style={styles.cta}
          testID="location-permission-banner-cta"
          accessibilityRole="button"
        >
          <Text style={styles.ctaTxt}>{t('common.location_permission.cta')}</Text>
        </Pressable>
        {onDismiss && (
          <Pressable
            onPress={onDismiss}
            style={styles.dismiss}
            testID="location-permission-banner-dismiss"
            accessibilityLabel={t('common.location_permission.dismiss')}
            accessibilityRole="button"
            hitSlop={8}
          >
            <Text style={styles.dismissTxt}>×</Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: 12,
    borderRadius: 14,
    backgroundColor: 'rgba(251,146,60,0.12)',
    borderWidth: 1,
    borderColor: 'rgba(251,146,60,0.32)',
    marginBottom: 10,
  },
  icon: { fontSize: 18 },
  body: { flex: 1, gap: 2 },
  title: { color: '#FB923C', fontSize: 13, fontWeight: '800' },
  desc: { color: 'rgba(255,255,255,0.75)', fontSize: 12, lineHeight: 16 },
  actions: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  cta: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 9,
    backgroundColor: 'rgba(251,146,60,0.22)',
    borderWidth: 1,
    borderColor: 'rgba(251,146,60,0.45)',
  },
  ctaTxt: { color: '#FB923C', fontSize: 12, fontWeight: '800' },
  dismiss: {
    width: 24,
    height: 24,
    alignItems: 'center',
    justifyContent: 'center',
  },
  dismissTxt: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: 18,
    fontWeight: '800',
  },
});
