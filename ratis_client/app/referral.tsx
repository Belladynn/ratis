// ratis_client/app/referral.tsx
//
// Referral screen — reached from Profil via `router.push('/referral')`. Three
// sections:
//   1. "Ton code"    — code card + Copy-to-clipboard + Share (native sheet)
//   2. "Tes stats"   — 3 tiles (signups, subscribers, CAB earned)
//   3. "Tes parrainages" — list of filleuls with status badge + display_name
//
// Privacy (RGPD) : the backend returns only display_name for each filleul,
// never email or user_id. The UI renders that directly — no additional
// identifying info gets mixed in client-side.

import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as Clipboard from "expo-clipboard";
import { useRouter } from "expo-router";
import { useTranslation } from "react-i18next";
import { ScreenBackground } from "@/components/ui/screen-background-legacy";
import { useReferralCode } from "@/hooks/use-referral-code";
import {
  useReferralHistory,
  type ReferralUse,
} from "@/hooks/use-referral-history";

const COPIED_TOAST_MS = 1500;

export default function ReferralScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const codeQuery = useReferralCode();
  const historyQuery = useReferralHistory();
  const [copiedVisible, setCopiedVisible] = useState(false);

  const code = codeQuery.data?.code ?? "";

  const handleCopy = useCallback(() => {
    if (!code) return;
    void Clipboard.setStringAsync(code).then(() => {
      setCopiedVisible(true);
      setTimeout(() => setCopiedVisible(false), COPIED_TOAST_MS);
    });
  }, [code]);

  const handleShare = useCallback(() => {
    if (!code) return;
    void Share.share({
      message: t("profil.referral_screen.share_message", { code }),
    });
  }, [code, t]);

  // ── Loading gate ─────────────────────────────────────────────────────────
  if (codeQuery.isLoading) {
    return (
      <View style={styles.container}>
        <ScreenBackground />
        <SafeAreaView edges={["top"]} style={styles.centerFlex}>
          <View testID="referral-loading" style={styles.centerFlex}>
            <ActivityIndicator color="#A78BFA" />
            <Text style={styles.centerText}>
              {t("profil.referral_screen.loading")}
            </Text>
          </View>
        </SafeAreaView>
      </View>
    );
  }

  // ── Error gate ───────────────────────────────────────────────────────────
  if (codeQuery.isError) {
    return (
      <View style={styles.container}>
        <ScreenBackground />
        <SafeAreaView edges={["top"]} style={styles.centerFlex}>
          <View testID="referral-error" style={styles.centerFlex}>
            <Text style={styles.errorTitle}>
              {t("profil.referral_screen.error_title")}
            </Text>
            <Pressable
              testID="referral-error-retry"
              style={styles.retryBtn}
              onPress={() => {
                void codeQuery.refetch();
                void historyQuery.refetch();
              }}
            >
              <Text style={styles.retryTxt}>
                {t("profil.referral_screen.error_retry")}
              </Text>
            </Pressable>
          </View>
        </SafeAreaView>
      </View>
    );
  }

  const stats = historyQuery.data?.stats;
  const uses = historyQuery.data?.uses ?? [];

  return (
    <View style={styles.container}>
      <ScreenBackground />
      <SafeAreaView edges={["top"]} style={{ flex: 1 }}>
        {/* Header */}
        <View style={styles.header}>
          <Pressable
            testID="referral-back"
            accessibilityRole="button"
            accessibilityLabel={t("profil.referral_screen.back")}
            onPress={() => router.back()}
            style={styles.backBtn}
          >
            <Text style={styles.backTxt}>‹</Text>
          </Pressable>
          <Text style={styles.title}>{t("profil.referral_screen.title")}</Text>
          <View style={styles.backBtn} />
        </View>

        <ScrollView contentContainerStyle={styles.content}>
          {/* ── Section Ton code ──────────────────────────────────────── */}
          <Text style={styles.section}>
            {t("profil.referral_screen.section_code")}
          </Text>
          <View style={styles.codeCard}>
            <Text testID="referral-code-value" style={styles.codeValue}>
              {code}
            </Text>
            <Text style={styles.codeHint}>
              {t("profil.referral_screen.code_hint")}
            </Text>
            <View style={styles.codeActions}>
              <Pressable
                testID="referral-copy"
                onPress={handleCopy}
                style={styles.actionBtn}
              >
                <Text style={styles.actionTxt}>
                  📋 {t("profil.referral_screen.copy_button")}
                </Text>
              </Pressable>
              <Pressable
                testID="referral-share"
                onPress={handleShare}
                style={[styles.actionBtn, styles.actionBtnPrimary]}
              >
                <Text style={[styles.actionTxt, styles.actionTxtPrimary]}>
                  📤 {t("profil.referral_screen.share_button")}
                </Text>
              </Pressable>
            </View>
            {copiedVisible && (
              <Text testID="referral-copied-toast" style={styles.toast}>
                {t("profil.referral_screen.copied_toast")}
              </Text>
            )}
          </View>

          {/* ── Section Stats ─────────────────────────────────────────── */}
          <Text style={[styles.section, styles.sectionSpacer]}>
            {t("profil.referral_screen.section_stats")}
          </Text>
          <View style={styles.statsRow}>
            <StatTile
              testID="referral-stat-signups"
              value={stats?.total_uses ?? 0}
              label={t("profil.referral_screen.stats_signups")}
            />
            <StatTile
              testID="referral-stat-subscribers"
              value={stats?.rewarded_uses ?? 0}
              label={t("profil.referral_screen.stats_subscribers")}
            />
            <StatTile
              testID="referral-stat-cab-earned"
              value={stats?.total_cab_earned ?? 0}
              label={t("profil.referral_screen.stats_cab_earned")}
              accent
            />
          </View>

          {/* ── Section Historique ────────────────────────────────────── */}
          <Text style={[styles.section, styles.sectionSpacer]}>
            {t("profil.referral_screen.section_history")}
          </Text>
          {uses.length === 0 ? (
            <View testID="referral-history-empty" style={styles.emptyCard}>
              <Text style={styles.emptyTxt}>
                {t("profil.referral_screen.history_empty")}
              </Text>
            </View>
          ) : (
            uses.map((use) => (
              <ReferralHistoryRow key={use.created_at} use={use} t={t} />
            ))
          )}
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function StatTile({
  testID,
  value,
  label,
  accent,
}: {
  testID: string;
  value: number;
  label: string;
  accent?: boolean;
}) {
  return (
    <View style={[styles.statTile, accent && styles.statTileAccent]}>
      <Text
        testID={testID}
        style={[styles.statValue, accent && styles.statValueAccent]}
      >
        {value}
      </Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

function ReferralHistoryRow({
  use,
  t,
}: {
  use: ReferralUse;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  const displayName =
    use.referred_user_display_name ?? t("profil.referral_screen.unnamed_user");

  let statusLabel: string;
  let statusStyle = styles.statusPending;
  if (use.status === "rewarded" && use.plan === "monthly") {
    statusLabel = t("profil.referral_screen.status_rewarded_monthly");
    statusStyle = styles.statusRewarded;
  } else if (use.status === "rewarded" && use.plan === "annual") {
    statusLabel = t("profil.referral_screen.status_rewarded_annual");
    statusStyle = styles.statusRewardedGold;
  } else {
    statusLabel = t("profil.referral_screen.status_pending");
  }

  return (
    <View style={styles.historyRow}>
      <View style={styles.historyNameCol}>
        <Text style={styles.historyName}>{displayName}</Text>
      </View>
      <View style={[styles.statusBadge, statusStyle]}>
        <Text style={styles.statusTxt}>{statusLabel}</Text>
      </View>
    </View>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0a0f12" },
  centerFlex: { flex: 1, alignItems: "center", justifyContent: "center" },
  centerText: { color: "rgba(255,255,255,0.6)", fontSize: 13, marginTop: 10 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  backBtn: {
    width: 40,
    height: 40,
    alignItems: "center",
    justifyContent: "center",
  },
  backTxt: { color: "#fff", fontSize: 28, lineHeight: 28 },
  title: {
    fontSize: 17,
    fontWeight: "900",
    color: "#fff",
    letterSpacing: -0.3,
  },
  content: { padding: 18, paddingBottom: 80, gap: 4 },
  section: {
    fontSize: 11,
    fontWeight: "800",
    color: "rgba(255,255,255,0.55)",
    textTransform: "uppercase",
    letterSpacing: 0.8,
    marginBottom: 8,
  },
  sectionSpacer: { marginTop: 24 },
  codeCard: {
    backgroundColor: "rgba(139,92,246,0.12)",
    borderWidth: 1,
    borderColor: "rgba(139,92,246,0.3)",
    borderRadius: 16,
    padding: 18,
    alignItems: "center",
    gap: 10,
  },
  codeValue: {
    fontSize: 28,
    fontWeight: "900",
    color: "#fff",
    letterSpacing: 3,
  },
  codeHint: {
    fontSize: 12,
    color: "rgba(255,255,255,0.6)",
    textAlign: "center",
    lineHeight: 18,
  },
  codeActions: { flexDirection: "row", gap: 10, marginTop: 6 },
  actionBtn: {
    flex: 1,
    backgroundColor: "rgba(255,255,255,0.08)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.12)",
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: "center",
  },
  actionBtnPrimary: {
    backgroundColor: "#A78BFA",
    borderColor: "#A78BFA",
  },
  actionTxt: { color: "#fff", fontSize: 13, fontWeight: "700" },
  actionTxtPrimary: { color: "#0a0f12", fontWeight: "900" },
  toast: {
    color: "#7DD3A4",
    fontSize: 12,
    fontWeight: "700",
    marginTop: 2,
  },
  statsRow: { flexDirection: "row", gap: 10 },
  statTile: {
    flex: 1,
    backgroundColor: "rgba(255,255,255,0.04)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
    borderRadius: 14,
    padding: 14,
    alignItems: "center",
    gap: 4,
  },
  statTileAccent: {
    backgroundColor: "rgba(255,184,0,0.12)",
    borderColor: "rgba(255,184,0,0.4)",
  },
  statValue: { fontSize: 20, fontWeight: "900", color: "#fff" },
  statValueAccent: { color: "#FFB800" },
  statLabel: {
    fontSize: 10,
    fontWeight: "700",
    color: "rgba(255,255,255,0.55)",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    textAlign: "center",
  },
  emptyCard: {
    backgroundColor: "rgba(255,255,255,0.03)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.06)",
    borderRadius: 14,
    padding: 20,
    alignItems: "center",
  },
  emptyTxt: {
    fontSize: 13,
    color: "rgba(255,255,255,0.5)",
    textAlign: "center",
  },
  historyRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: "rgba(255,255,255,0.04)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
    borderRadius: 12,
    padding: 12,
    marginBottom: 8,
  },
  historyNameCol: { flex: 1 },
  historyName: { fontSize: 14, fontWeight: "700", color: "#fff" },
  statusBadge: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 8,
  },
  statusPending: { backgroundColor: "rgba(139,92,246,0.2)" },
  statusRewarded: { backgroundColor: "rgba(125,211,164,0.22)" },
  statusRewardedGold: { backgroundColor: "rgba(255,184,0,0.22)" },
  statusTxt: { fontSize: 11, fontWeight: "700", color: "#fff" },
  retryBtn: {
    marginTop: 16,
    backgroundColor: "#A78BFA",
    borderRadius: 12,
    paddingVertical: 12,
    paddingHorizontal: 24,
  },
  retryTxt: { color: "#0a0f12", fontWeight: "900", fontSize: 14 },
  errorTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: "#fff",
    textAlign: "center",
    paddingHorizontal: 40,
  },
});
