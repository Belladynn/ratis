import React from "react";
import { View, Text, StyleSheet } from "react-native";

type Props = { message: string };

export function ErrorBanner({ message }: Props) {
  if (!message) return null;
  return (
    <View testID="error-banner" style={styles.container}>
      <Text style={styles.text}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: "#FEE2E2",
    borderColor: "#FCA5A5",
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginHorizontal: 24,
    marginVertical: 8,
  },
  text: { color: "#991B1B", fontSize: 14, textAlign: "center" },
});
