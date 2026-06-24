import React from "react";
import { View, Text, Pressable, StyleSheet, Linking } from "react-native";
import { useTranslation } from "react-i18next";
import { LEGAL_URLS } from "@/constants/Legal";

export function LegalFooter() {
  const { t } = useTranslation();

  return (
    <View style={styles.container}>
      <Text style={styles.text}>
        {t("auth.legal_intro")}{" "}
        <Pressable testID="legal-cgu" onPress={() => Linking.openURL(LEGAL_URLS.cgu)}>
          <Text style={styles.link}>{t("auth.legal_cgu")}</Text>
        </Pressable>
        {" "}{t("auth.legal_and")}{" "}
        <Pressable testID="legal-privacy" onPress={() => Linking.openURL(LEGAL_URLS.privacy)}>
          <Text style={styles.link}>{t("auth.legal_privacy")}</Text>
        </Pressable>
        .
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { paddingHorizontal: 24, paddingVertical: 12 },
  text: { fontSize: 12, color: "#666", textAlign: "center", lineHeight: 18 },
  link: { textDecorationLine: "underline", color: "#333" },
});
