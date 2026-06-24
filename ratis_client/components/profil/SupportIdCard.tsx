// ratis_client/components/profil/SupportIdCard.tsx
//
// Displays the user's public support identifier (RTS-XXXXXX format) on the
// profil screen. The user can copy it to clipboard to paste in a support
// message (e.g. Twitter DM, email). It is non-PII by design — the backend
// generates 6 chars from a 32-char alphabet (no I/O/0/1) and stores it on
// users.support_id (see PR #234). Showing it instead of email/UUID lets the
// user identify themselves to support without leaking PII.

import React, { useCallback, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import * as Clipboard from "expo-clipboard";
import { useTranslation } from "react-i18next";

const COPIED_TOAST_MS = 2000;

interface Props {
  support_id: string;
}

export function SupportIdCard({ support_id }: Props) {
  const { t } = useTranslation();
  const [copiedVisible, setCopiedVisible] = useState(false);

  const handleCopy = useCallback(() => {
    void Clipboard.setStringAsync(support_id).then(() => {
      setCopiedVisible(true);
      setTimeout(() => setCopiedVisible(false), COPIED_TOAST_MS);
    });
  }, [support_id]);

  const copyLabel = t("profil.support_id.copy");

  return (
    <View style={styles.card} testID="support-id-card">
      <Text style={styles.title}>{t("profil.support_id.title")}</Text>
      <Text style={styles.description}>
        {t("profil.support_id.description")}
      </Text>
      <View style={styles.row}>
        <Text
          testID="support-id-value"
          style={styles.value}
          accessibilityLabel={t("profil.support_id.value_a11y", {
            id: support_id,
          })}
        >
          {support_id}
        </Text>
        <Pressable
          testID="support-id-copy"
          onPress={handleCopy}
          accessibilityRole="button"
          accessibilityLabel={copyLabel}
          accessibilityHint={t("profil.support_id.copy_a11y_hint")}
          style={({ pressed }) => [styles.copyBtn, pressed && styles.copyBtnPressed]}
        >
          <Text style={styles.copyTxt}>📋 {copyLabel}</Text>
        </Pressable>
      </View>
      {copiedVisible && (
        <Text testID="support-id-copied-toast" style={styles.toast}>
          {t("profil.support_id.copied")}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "rgba(77,212,179,0.08)",
    borderWidth: 1,
    borderColor: "rgba(77,212,179,0.28)",
    borderRadius: 16,
    padding: 14,
    marginBottom: 12,
  },
  title: {
    fontSize: 11,
    fontWeight: "800",
    color: "rgba(255,255,255,0.7)",
    textTransform: "uppercase",
    letterSpacing: 0.8,
    marginBottom: 4,
  },
  description: {
    fontSize: 11,
    fontStyle: "italic",
    color: "rgba(255,255,255,0.55)",
    lineHeight: 15,
    marginBottom: 10,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
  },
  value: {
    flex: 1,
    fontSize: 20,
    fontWeight: "900",
    color: "#4DD4B3",
    letterSpacing: 2,
    fontFamily: "Courier",
  },
  copyBtn: {
    backgroundColor: "rgba(255,255,255,0.08)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.14)",
    borderRadius: 10,
    paddingVertical: 8,
    paddingHorizontal: 12,
  },
  copyBtnPressed: {
    backgroundColor: "rgba(255,255,255,0.14)",
  },
  copyTxt: { color: "#fff", fontSize: 12, fontWeight: "700" },
  toast: {
    marginTop: 8,
    color: "#7DD3A4",
    fontSize: 12,
    fontWeight: "700",
    textAlign: "right",
  },
});
