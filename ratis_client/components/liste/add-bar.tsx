/**
 * Liste — AddBar (V5 strict iso + wave-4 search autocomplete + wave-12
 * default suggestions).
 *
 * Reference visual : `Ratis_handoff/screenshots/V5-FINAL-iso/Liste Courses.png`.
 * Reference JSX    : `Ratis_handoff/lib/ratis-liste-ui.jsx` lines 154-245
 *                    (`AddBar`).
 *
 * Layout
 * ------
 *   [   input "Ajouter un produit…"            ] [💡] [✨] [🎤]
 *   └─ dropdown :
 *      * empty input + focused → 5 alphabetic default suggestions
 *        (wave 12 — PO ticket 2026-05-14).
 *      * typed input → debounced search hits (300 ms).
 *
 *  - Wrapper : 1.5px border (terracotta when focused, white/8% otherwise),
 *    radius 14, padding 6, slight inset highlight.
 *  - Three emoji icon buttons open dedicated bottom sheets.
 *  - Bug 3 (wave 4) : the input now drives a debounced
 *    ``useProductSearch`` hook. A tap on a result fires ``onSelectHit``
 *    so the parent can POST AddItem with the EAN ; the input then
 *    clears.
 *  - Wave 7 (PO ticket 2026-05-13 follow-up) : the « + » submit button
 *    has been REMOVED entirely. PO directive verbatim « J'aurai bien
 *    enlevé le + en vrai et juste appuyer sur l'item dans la liste
 *    déroulante de la recherche ça l'ajoute directement ». The only
 *    add-to-list path is now tapping a dropdown hit (or pressing
 *    keyboard return when a hit is available — same fallthrough as a
 *    tap). The 💡 IconBtn keeps its own path to open the
 *    SuggestionsSheet, untouched by this change. ``onSubmit`` prop
 *    survives only as the keyboard-Enter no-hit fallback ; the parent
 *    screen wires it to a no-op.
 *  - Wave 6 (Issue 2) : ``console.warn`` instrumentation gated behind
 *    ``__DEV__ || EXPO_PUBLIC_TRACE_LIST=1`` traces the AddItem tap
 *    flow (focus → hit list size → pick) so PO can capture device logs
 *    via ``npx react-native log-android`` (or Sentry breadcrumbs in
 *    prod) when « tap doesn't add ». Wave-5 PR #430 fixed the
 *    onPress→onPressIn race; if PO still hits the issue this telemetry
 *    pinpoints whether the handler reaches the parent at all.
 *
 * Token derogation : numeric values come straight from the JSX iso source —
 * see `chunk-3-followups.md` § 10 for the rationale.
 */

import React, { useState } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useTranslation } from 'react-i18next';

import {
  useProductSearch,
  type ProductSearchHit,
} from '@/hooks/use-product-search';
import { useDefaultSuggestions } from '@/hooks/use-default-suggestions';
import { composeSearchHitSecondary } from '@/utils/product-search-hit';

const MAX_SEARCH_HITS_IN_DROPDOWN = 8;

// Wave 6 Issue 2 — instrumentation gate. ``__DEV__`` covers
// development bundles; the explicit EXPO_PUBLIC_TRACE_LIST=1 env knob
// lets PO opt-in on a production OTA without rebuilding the binary
// (Expo inlines EXPO_PUBLIC_* at bundle time so toggling it requires a
// new OTA push, but it survives release/profile mode unlike __DEV__).
const TRACE_ADDBAR =
  (typeof __DEV__ !== 'undefined' && __DEV__) ||
  process.env.EXPO_PUBLIC_TRACE_LIST === '1';

function trace(event: string, payload?: Record<string, unknown>) {
  if (!TRACE_ADDBAR) return;
  // ``console.warn`` (not ``log``) so React Native LogBox surfaces it
  // and ``react-native log-android`` / ``log-ios`` capture it by
  // default. Sentry breadcrumbs also pick it up in prod when the OTA
  // ships with ``EXPO_PUBLIC_TRACE_LIST=1``.
  console.warn(`[AddBar] ${event}`, payload ?? {});
}

export type AddBarProps = {
  /**
   * Backwards-compat fallback fired with the trimmed input when the
   * keyboard ``return`` key is pressed AND no dropdown hit is available.
   * Wave 7 (PO ticket 2026-05-13 follow-up) removed the « + » button
   * entirely ; only the keyboard-Enter path can still trigger this.
   * Most callers wire it to a no-op (legacy suggestion-sheet path
   * retired).
   */
  onSubmit: (name: string) => void;
  onPressSuggestions: () => void;
  onPressTemplates: () => void;
  onPressVoice: () => void;
  /**
   * Bug 3 (wave 4) — fires when the user taps a search hit in the
   * dropdown OR presses keyboard-return with hits available (wave 7).
   * The parent owns the AddItem mutation : it receives the picked
   * product so it can POST ``/lists/{id}/items`` with the EAN.
   */
  onSelectHit?: (hit: ProductSearchHit) => void;
  testID?: string;
};

export function AddBar({
  onSubmit,
  onPressSuggestions,
  onPressTemplates,
  onPressVoice,
  onSelectHit,
  testID,
}: AddBarProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [focused, setFocused] = useState(false);

  // Wave 13 (PO ticket 2026-05-14 follow-up) — empty input is served
  // by the dedicated ``useDefaultSuggestions`` hook (tier-composed
  // server-side : user history + curated FR staples) ; typed input
  // hits the debounced ``useProductSearch``. Both hooks are always
  // mounted (rules of hooks) ; a simple ``q===''`` switch picks the
  // data source for the dropdown. See
  // ``docs/superpowers/specs/2026-05-14-default-search-3tier-design.md``.
  const trimmed = query.trim();
  const isDefaultMode = trimmed.length === 0;
  const search = useProductSearch(query, {
    limit: MAX_SEARCH_HITS_IN_DROPDOWN,
    enabled: !isDefaultMode,
  });
  const suggestions = useDefaultSuggestions({ limit: 5 });
  const hits = isDefaultMode
    ? (suggestions.data?.items ?? [])
    : (search.data?.items ?? []);
  const isFetching = isDefaultMode
    ? !!suggestions.isFetching
    : !!search.isFetching;
  // Dropdown visible whenever the input has focus. Wave 12 dropped the
  // « ≥ 2 chars » gate so the default-suggestions list can populate on
  // focus alone. Empty-state row (« Aucun produit trouvé ») only fires
  // when the user has actually typed something.
  const showDropdown = focused;
  const empty = !isFetching && hits.length === 0 && !isDefaultMode;

  // Wave 7 — submit is now only reached via keyboard return on the
  // TextInput (the « + » button was removed per PO directive). Picks
  // the first dropdown hit when one is available, else falls back to
  // ``onSubmit`` with the raw text (parent wires that to a no-op).
  const submit = () => {
    const value = query.trim();
    if (!value) return;
    if (hits.length > 0) {
      trace('submit_picks_first_hit', {
        query: value,
        ean: hits[0].ean,
        name: hits[0].name,
      });
      onSelectHit?.(hits[0]);
      setQuery('');
      return;
    }
    trace('submit_fallback_text', { query: value });
    onSubmit(value);
    setQuery('');
  };

  const pickHit = (hit: ProductSearchHit) => {
    trace('pick_hit', {
      ean: hit.ean,
      name: hit.name,
      hits_count: hits.length,
      onSelectHit_wired: !!onSelectHit,
    });
    onSelectHit?.(hit);
    setQuery('');
  };

  return (
    <View testID={testID ?? 'liste-add-bar-wrap'}>
      <View
        testID="liste-add-bar"
        style={[
          styles.wrap,
          focused ? styles.wrapFocused : styles.wrapBlurred,
        ]}
      >
        <TextInput
          testID="liste-add-bar-input"
          value={query}
          onChangeText={(next) => {
            setQuery(next);
            if (next.trim().length >= 2) {
              trace('query_change', { len: next.trim().length });
            }
          }}
          onFocus={() => {
            setFocused(true);
            trace('focus');
          }}
          // 250 ms delay so a tap on a dropdown row is registered before
          // the blur tears it down. The hit row also fires its handler
          // on ``onPressIn`` (touch-down) rather than ``onPress``
          // (touch-up) so the parent receives the event even on slow
          // taps — see the dropdown ``Pressable`` below.
          onBlur={() => setTimeout(() => setFocused(false), 250)}
          onSubmitEditing={submit}
          returnKeyType="done"
          placeholder={t('liste.add_bar.placeholder')}
          placeholderTextColor="rgba(255,255,255,0.35)"
          style={styles.input}
        />
        <IconBtn
          testID="liste-add-bar-suggestions"
          emoji="💡"
          accessibilityLabel={t('liste.add_bar.suggestions_label')}
          onPress={onPressSuggestions}
        />
        <IconBtn
          testID="liste-add-bar-templates"
          emoji="✨"
          accessibilityLabel={t('liste.add_bar.templates_label')}
          onPress={onPressTemplates}
        />
        <IconBtn
          testID="liste-add-bar-voice"
          emoji="🎤"
          accessibilityLabel={t('liste.add_bar.voice_label')}
          onPress={onPressVoice}
        />
      </View>
      {showDropdown ? (
        <View
          testID="liste-add-bar-dropdown"
          style={styles.dropdown}
        >
          {isDefaultMode && hits.length > 0 ? (
            <Text
              testID="liste-add-bar-suggestions-eyebrow"
              style={styles.suggestionsEyebrow}
            >
              {t('liste.add_bar.suggestions_eyebrow')}
            </Text>
          ) : null}
          {hits.map((hit) => {
            // Wave 9 — compose secondary line « brand · qty · 🇫🇷 · 🌱 »
            // so PO can distinguish 8 identical-named « Pomme de terre »
            // hits without tapping each one. ``null`` collapses the row
            // back to single-line (no empty Text eating vertical space).
            const secondary = composeSearchHitSecondary(hit);
            return (
              <Pressable
                key={hit.ean}
                testID={`liste-add-bar-hit-${hit.ean}`}
                accessibilityRole="button"
                accessibilityLabel={
                  secondary ? `${hit.name} — ${secondary}` : hit.name
                }
                // ``onPressIn`` (touch-down) instead of ``onPress``
                // (touch-up) : the TextInput above fires ``onBlur`` as
                // soon as the touch lands on the dropdown row, which
                // schedules ``setFocused(false)`` after 250 ms — slower
                // than many real-device taps. Firing on touch-down
                // guarantees the handler reaches the parent before the
                // dropdown can unmount. Hit-slop expands the touchable
                // area for accidental partial taps near row borders.
                onPressIn={() => pickHit(hit)}
                hitSlop={6}
                style={styles.hitRow}
              >
                <Text style={styles.hitName} numberOfLines={1}>
                  {hit.name}
                </Text>
                {secondary ? (
                  <Text
                    testID={`liste-add-bar-hit-${hit.ean}-secondary`}
                    style={styles.hitSecondary}
                    numberOfLines={1}
                  >
                    {secondary}
                  </Text>
                ) : null}
              </Pressable>
            );
          })}
          {empty ? (
            <View testID="liste-add-bar-empty" style={styles.hitRow}>
              <Text style={styles.hitEmpty}>
                {t('liste.add_bar.search_empty')}
              </Text>
            </View>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

type IconBtnProps = {
  emoji: string;
  accessibilityLabel: string;
  onPress: () => void;
  testID?: string;
};

function IconBtn({ emoji, accessibilityLabel, onPress, testID }: IconBtnProps) {
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      onPress={onPress}
      style={styles.iconBtn}
      hitSlop={4}
    >
      <Text style={styles.iconTxt}>{emoji}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    padding: 6,
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderRadius: 14,
    borderWidth: 1.5,
  },
  wrapFocused: {
    borderColor: 'rgba(218,119,86,0.55)',
  },
  wrapBlurred: {
    borderColor: 'rgba(255,255,255,0.08)',
  },
  input: {
    flex: 1,
    minWidth: 0,
    color: '#fff',
    fontSize: 13,
    fontWeight: '700',
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  iconBtn: {
    width: 36,
    height: 36,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    backgroundColor: 'rgba(255,255,255,0.04)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  iconTxt: {
    fontSize: 14,
  },
  dropdown: {
    marginTop: 6,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    backgroundColor: 'rgba(20,30,38,0.96)',
    overflow: 'hidden',
  },
  hitRow: {
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  hitName: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '700',
  },
  hitSecondary: {
    // Wave 9 — secondary line composes brand · quantity · 🇫🇷 · 🌱.
    // No uppercase / letter-spacing because emoji rendering loses
    // fidelity under those transforms (the 🇫🇷 regional indicator pair
    // and the 🌱 leaf both get visually clipped). Casing also stays
    // mixed because raw quantities (« 1 kg » / « 6 x 33 cl ») read
    // worst in upper-case.
    color: 'rgba(255,255,255,0.55)',
    fontSize: 11,
    fontWeight: '600',
    marginTop: 2,
  },
  hitEmpty: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 12,
    fontStyle: 'italic',
  },
  // Wave 12 — small terracotta eyebrow above the default-mode suggestion
  // list so the user understands the dropdown is « Suggestions » (top-5
  // alpha) rather than search hits. Hidden as soon as they start typing.
  suggestionsEyebrow: {
    paddingHorizontal: 12,
    paddingTop: 8,
    paddingBottom: 4,
    color: 'rgba(218,119,86,0.85)',
    fontSize: 9,
    fontWeight: '800',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
});

export default AddBar;
