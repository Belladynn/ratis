// ratis_client/app/my-info.tsx
//
// Profile edit screen — routed outside the tab stack. Reached from the Profil
// tab via `router.push('/my-info')`. Editable sections:
//  1. display_name (1..30 chars) — persisted via PATCH /account/profile
//  2. timezone (IANA string) — same endpoint
//
// Email is displayed read-only — the backend model treats it as immutable,
// and changing email on an OAuth-linked account would break the provider
// mapping.

import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { ScreenBackground } from '@/components/ui/screen-background-legacy';
import { useAuthMe } from '@/hooks/use-auth-me';
import { useUpdateProfile } from '@/hooks/use-update-profile';
import { useIdentities, useLinkProvider, useUnlinkProvider } from '@/hooks/use-identities';
import { useAuth } from '@/hooks/useAuth';

/** OAuth providers a user can link to their account. */
const LINKABLE_PROVIDERS = ['google', 'apple'] as const;
type LinkableProvider = (typeof LINKABLE_PROVIDERS)[number];

const DISPLAY_NAME_MIN = 1;
const DISPLAY_NAME_MAX = 30;

function isValidDisplayName(v: string): boolean {
  const trimmed = v.trim();
  return trimmed.length >= DISPLAY_NAME_MIN && trimmed.length <= DISPLAY_NAME_MAX;
}

function isValidTimezone(v: string): boolean {
  // Loose client-side check — the real validation happens server-side via
  // Python's zoneinfo. We just prevent trivially broken input.
  return v.trim().length > 0 && v.trim().length <= 64;
}

export default function MyInfoScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const { data: user } = useAuthMe();
  const updateProfile = useUpdateProfile();

  const { getProviderToken } = useAuth();
  const { data: identities } = useIdentities();
  const linkProvider = useLinkProvider();
  const unlinkProvider = useUnlinkProvider();

  const [displayName, setDisplayName] = useState('');
  const [timezone, setTimezone] = useState('');
  const [profileFeedback, setProfileFeedback] = useState<string | null>(null);
  const [linkedFeedback, setLinkedFeedback] = useState<string | null>(null);
  const [busyProvider, setBusyProvider] = useState<LinkableProvider | null>(null);

  // Prefill the form with the current user values whenever they load / change.
  useEffect(() => {
    if (!user) return;
    setDisplayName(user.display_name ?? '');
    setTimezone(user.timezone);
  }, [user]);

  const initialDisplayName = user?.display_name ?? '';
  const initialTimezone = user?.timezone ?? '';

  const displayNameChanged = displayName !== initialDisplayName;
  const timezoneChanged = timezone !== initialTimezone;
  const profileDirty = displayNameChanged || timezoneChanged;
  const profileValid =
    (!displayNameChanged || isValidDisplayName(displayName)) &&
    (!timezoneChanged || isValidTimezone(timezone));
  const canSaveProfile = profileDirty && profileValid && !updateProfile.isPending;

  const handleSaveProfile = async () => {
    if (!canSaveProfile) return;
    setProfileFeedback(null);
    try {
      const payload: { display_name?: string; timezone?: string } = {};
      if (displayNameChanged) payload.display_name = displayName.trim();
      if (timezoneChanged) payload.timezone = timezone.trim();
      await updateProfile.mutateAsync(payload);
      setProfileFeedback(t('profil.my_info.saved'));
    } catch (err) {
      setProfileFeedback(mapError(err, t));
    }
  };

  const linkedProviders = new Set((identities ?? []).map((i) => i.provider));

  const handleLink = async (provider: LinkableProvider) => {
    if (busyProvider) return;
    setLinkedFeedback(null);
    setBusyProvider(provider);
    try {
      const token = await getProviderToken(provider);
      await linkProvider.mutateAsync({ provider, token });
      setLinkedFeedback(
        t('profil.my_info.linked_accounts.linked_success', {
          provider: t(`profil.my_info.linked_accounts.provider_${provider}`),
        }),
      );
    } catch (err) {
      // A user-cancelled native prompt is not an error worth surfacing.
      if (isCancelled(err)) return;
      setLinkedFeedback(mapLinkError(err, t));
    } finally {
      setBusyProvider(null);
    }
  };

  const handleUnlink = (provider: LinkableProvider) => {
    if (busyProvider) return;
    Alert.alert(
      t('profil.my_info.linked_accounts.confirm_unlink'),
      undefined,
      [
        { text: t('profil.my_info.back'), style: 'cancel' },
        {
          text: t('profil.my_info.linked_accounts.unlink_button'),
          style: 'destructive',
          onPress: () => {
            void doUnlink(provider);
          },
        },
      ],
    );
  };

  const doUnlink = async (provider: LinkableProvider) => {
    setLinkedFeedback(null);
    setBusyProvider(provider);
    try {
      await unlinkProvider.mutateAsync(provider);
      setLinkedFeedback(
        t('profil.my_info.linked_accounts.unlinked_success', {
          provider: t(`profil.my_info.linked_accounts.provider_${provider}`),
        }),
      );
    } catch (err) {
      setLinkedFeedback(mapLinkError(err, t));
    } finally {
      setBusyProvider(null);
    }
  };

  return (
    <View style={styles.container}>
      <ScreenBackground />
      <SafeAreaView edges={['top']} style={{ flex: 1 }}>
        {/* Header with back button */}
        <View style={styles.header}>
          <Pressable
            testID="my-info-back"
            accessibilityRole="button"
            accessibilityLabel={t('profil.my_info.back')}
            onPress={() => router.back()}
            style={styles.backBtn}
          >
            <Text style={styles.backTxt}>‹</Text>
          </Pressable>
          <Text style={styles.title}>{t('profil.my_info.title')}</Text>
          <View style={styles.backBtn} />
        </View>

        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {/* ── Profile section ──────────────────────────────────────────── */}
          <Text style={styles.section}>{t('profil.my_info.section_profile')}</Text>

          <Text style={styles.label}>{t('profil.my_info.email_label')}</Text>
          <Text testID="my-info-email" style={styles.emailReadonly}>
            {user?.email ?? ''}
          </Text>
          <Text style={styles.hint}>{t('profil.my_info.email_hint')}</Text>

          <Text style={styles.label}>{t('profil.my_info.display_name_label')}</Text>
          <TextInput
            testID="my-info-display-name"
            style={styles.input}
            value={displayName}
            onChangeText={setDisplayName}
            placeholder={t('profil.my_info.display_name_placeholder')}
            placeholderTextColor="rgba(255,255,255,0.3)"
            maxLength={DISPLAY_NAME_MAX}
            autoCapitalize="words"
          />
          <Text style={styles.hint}>{t('profil.my_info.display_name_hint')}</Text>

          <Text style={styles.label}>{t('profil.my_info.timezone_label')}</Text>
          <TextInput
            testID="my-info-timezone"
            style={styles.input}
            value={timezone}
            onChangeText={setTimezone}
            autoCapitalize="none"
            autoCorrect={false}
          />
          <Text style={styles.hint}>{t('profil.my_info.timezone_hint')}</Text>

          {profileFeedback !== null && (
            <Text testID="my-info-profile-feedback" style={styles.feedback}>
              {profileFeedback}
            </Text>
          )}

          <Pressable
            testID="my-info-save"
            accessibilityRole="button"
            accessibilityState={{ disabled: !canSaveProfile }}
            onPress={() => {
              void handleSaveProfile();
            }}
            disabled={!canSaveProfile}
            style={[styles.submitBtn, !canSaveProfile && styles.submitBtnDisabled]}
          >
            {updateProfile.isPending ? (
              <ActivityIndicator color="#0a0f12" />
            ) : (
              <Text style={styles.submitTxt}>{t('profil.my_info.save_button')}</Text>
            )}
          </Pressable>

          {/* ── Linked accounts section ──────────────────────────────────── */}
          <Text style={[styles.section, styles.sectionSpacer]}>
            {t('profil.my_info.linked_accounts.section_title')}
          </Text>
          <Text style={styles.hint}>
            {t('profil.my_info.linked_accounts.section_hint')}
          </Text>

          {LINKABLE_PROVIDERS.map((provider) => {
            const linked = linkedProviders.has(provider);
            const busy = busyProvider === provider;
            return (
              <View
                key={provider}
                testID={`linked-row-${provider}`}
                style={styles.linkRow}
              >
                <View style={styles.linkRowLabel}>
                  <Text style={styles.linkProvider}>
                    {t(`profil.my_info.linked_accounts.provider_${provider}`)}
                  </Text>
                  {linked && (
                    <Text testID={`linked-badge-${provider}`} style={styles.badge}>
                      {t('profil.my_info.linked_accounts.linked_badge')}
                    </Text>
                  )}
                </View>
                {linked ? (
                  <Pressable
                    testID={`unlink-btn-${provider}`}
                    accessibilityRole="button"
                    onPress={() => handleUnlink(provider)}
                    disabled={busy}
                    style={[styles.linkBtn, styles.unlinkBtn, busy && styles.linkBtnBusy]}
                  >
                    {busy ? (
                      <ActivityIndicator color="#fff" />
                    ) : (
                      <Text style={styles.unlinkBtnTxt}>
                        {t('profil.my_info.linked_accounts.unlink_button')}
                      </Text>
                    )}
                  </Pressable>
                ) : (
                  <Pressable
                    testID={`link-btn-${provider}`}
                    accessibilityRole="button"
                    onPress={() => {
                      void handleLink(provider);
                    }}
                    disabled={busy}
                    style={[styles.linkBtn, busy && styles.linkBtnBusy]}
                  >
                    {busy ? (
                      <ActivityIndicator color="#0a0f12" />
                    ) : (
                      <Text style={styles.linkBtnTxt}>
                        {t('profil.my_info.linked_accounts.link_button', {
                          provider: t(
                            `profil.my_info.linked_accounts.provider_${provider}`,
                          ),
                        })}
                      </Text>
                    )}
                  </Pressable>
                )}
              </View>
            );
          })}

          {linkedFeedback !== null && (
            <Text testID="linked-accounts-feedback" style={styles.feedback}>
              {linkedFeedback}
            </Text>
          )}
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

/** Translate backend error codes to user-friendly i18n strings. */
function mapError(err: unknown, t: (k: string) => string): string {
  const msg = err instanceof Error ? err.message : '';
  if (msg.includes('429') || msg.toLowerCase().includes('rate')) {
    return t('profil.my_info.error_rate_limited');
  }
  if (msg.includes('400') || msg.toLowerCase().includes('invalid')) {
    return t('profil.my_info.error_invalid_payload');
  }
  return t('profil.my_info.error_generic');
}

/** True when the error is a user-cancelled native sign-in prompt. */
function isCancelled(err: unknown): boolean {
  return (err as { code?: string })?.code === 'cancelled';
}

/**
 * Map link/unlink backend `detail` codes (surfaced as `AuthError.code` by the
 * api-client) to user-facing i18n strings.
 */
function mapLinkError(err: unknown, t: (k: string) => string): string {
  const code = (err as { code?: string })?.code;
  if (code === 'identity_already_linked') {
    return t('profil.my_info.linked_accounts.error_already_linked');
  }
  if (code === 'cannot_unlink_last_identity') {
    return t('profil.my_info.linked_accounts.error_cannot_unlink_last');
  }
  return t('profil.my_info.linked_accounts.error_generic');
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0a0f12' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  backBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  backTxt: { color: '#fff', fontSize: 28, lineHeight: 28 },
  title: { fontSize: 17, fontWeight: '900', color: '#fff', letterSpacing: -0.3 },
  content: { padding: 18, paddingBottom: 80, gap: 4 },
  section: {
    fontSize: 11,
    fontWeight: '800',
    color: 'rgba(255,255,255,0.55)',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: 6,
  },
  label: {
    fontSize: 12,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.75)',
    marginTop: 12,
  },
  emailReadonly: {
    fontSize: 15,
    color: '#fff',
    paddingVertical: 10,
    paddingHorizontal: 2,
  },
  input: {
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: '#fff',
    fontSize: 15,
    marginTop: 6,
  },
  hint: { fontSize: 11, color: 'rgba(255,255,255,0.5)', marginTop: 4 },
  feedback: { fontSize: 13, fontWeight: '600', color: '#A78BFA', marginTop: 12 },
  submitBtn: {
    marginTop: 16,
    backgroundColor: '#A78BFA',
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: 'center',
  },
  submitBtnDisabled: { opacity: 0.4 },
  submitTxt: { color: '#0a0f12', fontWeight: '900', fontSize: 15, letterSpacing: -0.2 },
  sectionSpacer: { marginTop: 32 },
  linkRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    marginTop: 10,
  },
  linkRowLabel: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  linkProvider: { fontSize: 15, fontWeight: '700', color: '#fff' },
  badge: {
    fontSize: 10,
    fontWeight: '800',
    color: '#0a0f12',
    backgroundColor: '#34D399',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
    overflow: 'hidden',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  linkBtn: {
    backgroundColor: '#A78BFA',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 8,
    minWidth: 96,
    alignItems: 'center',
  },
  linkBtnTxt: { color: '#0a0f12', fontWeight: '800', fontSize: 13 },
  unlinkBtn: {
    backgroundColor: 'transparent',
    borderWidth: 1,
    borderColor: 'rgba(248,113,113,0.6)',
  },
  unlinkBtnTxt: { color: '#F87171', fontWeight: '800', fontSize: 13 },
  linkBtnBusy: { opacity: 0.5 },
});
