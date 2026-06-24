import { Tabs } from 'expo-router';

import { RatisTabBar } from '@/components/navigation/ratis-tab-bar';

export default function TabsLayout() {
  return (
    <Tabs
      tabBar={(props) => <RatisTabBar {...props} />}
      screenOptions={{ headerShown: false }}
    >
      <Tabs.Screen name="index" options={{ title: 'Accueil' }} />
      <Tabs.Screen name="liste" options={{ title: 'Liste' }} />
      <Tabs.Screen name="scan" options={{ title: 'Scan' }} />
      <Tabs.Screen name="produit" options={{ title: 'Produit' }} />
      <Tabs.Screen name="profil" options={{ title: 'Profil' }} />
    </Tabs>
  );
}
