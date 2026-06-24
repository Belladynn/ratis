import React from "react";
import { Pressable, Text, StyleSheet, ActivityIndicator, View } from "react-native";
import { useTranslation } from "react-i18next";

type Props = {
  onPress: () => void;
  disabled?: boolean;
  loading?: boolean;
};

export function GoogleButton({ onPress, disabled, loading }: Props) {
  const { t } = useTranslation();
  return (
    <Pressable
      testID="google-signin"
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        styles.btn,
        (disabled || loading) && styles.disabled,
        pressed && styles.pressed,
      ]}
    >
      <View style={styles.inner}>
        {loading ? (
          <ActivityIndicator testID="google-signin-spinner" color="#333" />
        ) : (
          <Text style={styles.label}>{t("auth.continue_with_google")}</Text>
        )}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  btn: {
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 12,
    paddingVertical: 14,
    paddingHorizontal: 20,
    alignItems: "center",
  },
  inner: { flexDirection: "row", alignItems: "center", gap: 8 },
  label: { fontSize: 16, fontWeight: "500", color: "#333" },
  disabled: { opacity: 0.5 },
  pressed: { opacity: 0.8 },
});
