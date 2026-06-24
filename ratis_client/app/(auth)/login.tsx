// ratis_client/app/(auth)/login.tsx

import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, Platform, Pressable } from "react-native";
import * as Apple from "expo-apple-authentication";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/hooks/useAuth";
import { GoogleButton } from "@/components/GoogleButton";
import { ErrorBanner } from "@/components/ErrorBanner";
import { LegalFooter } from "@/components/LegalFooter";

export default function LoginScreen() {
  const { t } = useTranslation();
  const auth = useAuth();
  const [appleAvailable, setAppleAvailable] = useState(false);

  useEffect(() => {
    if (Platform.OS === "ios") {
      Apple.isAvailableAsync().then(setAppleAvailable);
    }
  }, []);

  const signingIn = auth.status === "authenticating";
  const currentProvider = signingIn ? auth.provider : null;

  const errorMessage =
    auth.status === "unauthenticated" && auth.error
      ? t(`auth.${auth.error.code}`, { defaultValue: t("auth.unknown_error") })
      : "";

  return (
    <View style={styles.container}>
      <View style={styles.content}>
        <Text style={styles.title}>Ratis</Text>
        <Text style={styles.tagline}>{t("auth.tagline")}</Text>

        <ErrorBanner message={errorMessage} />

        <View style={styles.buttons}>
          {appleAvailable && (
            <Apple.AppleAuthenticationButton
              testID="apple-signin"
              buttonType={Apple.AppleAuthenticationButtonType.CONTINUE}
              buttonStyle={Apple.AppleAuthenticationButtonStyle.BLACK}
              cornerRadius={12}
              style={styles.appleButton}
              onPress={() => auth.signIn("apple")}
            />
          )}

          <GoogleButton
            onPress={() => auth.signIn("google")}
            disabled={signingIn}
            loading={signingIn && currentProvider === "google"}
          />

          {__DEV__ && (
            <>
              <View style={styles.devSeparator}>
                <View style={styles.devLine} />
                <Text style={styles.devLabel}>dev only</Text>
                <View style={styles.devLine} />
              </View>
              <Pressable
                style={styles.devButton}
                onPress={() => auth.devSignIn()}
                testID="dev-signin"
              >
                <Text style={styles.devButtonText}>⚡ Passer le login</Text>
              </Pressable>
            </>
          )}
        </View>
      </View>

      <LegalFooter />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#fff", justifyContent: "space-between" },
  content: { flex: 1, justifyContent: "center", paddingHorizontal: 24 },
  title: { fontSize: 48, fontWeight: "700", textAlign: "center", color: "#1F2937" },
  tagline: {
    fontSize: 16, color: "#6B7280", textAlign: "center",
    marginTop: 8, marginBottom: 48,
  },
  buttons: { gap: 12 },
  appleButton: { width: "100%", height: 48 },
  devSeparator: {
    flexDirection: "row", alignItems: "center", gap: 8, marginTop: 8,
  },
  devLine:  { flex: 1, height: 1, backgroundColor: "#E5E7EB" },
  devLabel: { fontSize: 11, color: "#9CA3AF", fontWeight: "500" },
  devButton: {
    borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 12,
    paddingVertical: 12, alignItems: "center",
  },
  devButtonText: { fontSize: 14, color: "#6B7280", fontWeight: "600" },
});
