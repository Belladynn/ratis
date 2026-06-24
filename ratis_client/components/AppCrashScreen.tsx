import React from "react";
import { Pressable, View, Text, StyleSheet } from "react-native";
import * as Updates from "expo-updates";
import { useTranslation } from "react-i18next";

export function AppCrashScreen() {
  const { t } = useTranslation();

  // Reload the JS bundle from scratch — the cheapest recovery path that does
  // not require the user to kill the app from the OS task switcher. Failures
  // are swallowed: there is nothing more we can do from a crashed tree.
  const onReload = () => {
    void Updates.reloadAsync().catch(() => undefined);
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>{t("crash.title")}</Text>
      <Text style={styles.body}>{t("crash.body")}</Text>
      <Pressable
        testID="app-crash-reload"
        accessibilityRole="button"
        onPress={onReload}
        style={({ pressed }) => [styles.button, pressed && styles.buttonPressed]}
      >
        <Text style={styles.buttonText}>{t("crash.reload")}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", alignItems: "center", padding: 24 },
  title: { fontSize: 32, fontWeight: "700", marginBottom: 12 },
  body: { fontSize: 16, color: "#6B7280", textAlign: "center", marginBottom: 24 },
  button: {
    backgroundColor: "#FF6B35",
    paddingVertical: 14,
    paddingHorizontal: 28,
    borderRadius: 12,
  },
  buttonPressed: { opacity: 0.85 },
  buttonText: { color: "#FFFFFF", fontSize: 16, fontWeight: "700" },
});
