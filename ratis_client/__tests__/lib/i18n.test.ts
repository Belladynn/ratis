/**
 * Tests for the i18n infrastructure.
 * Run: npx jest --no-coverage -t "i18n"
 */

jest.mock('expo-localization', () => ({
  getLocales: () => [{ languageCode: 'fr' }],
}));

// Import after mocking expo-localization
import i18n from '@/lib/i18n';

describe('i18n', () => {
  it('is initialized with French as the default language', () => {
    expect(i18n.isInitialized).toBe(true);
    expect(i18n.language).toBe('fr');
  });

  it('returns the correct string for a simple key', () => {
    expect(i18n.t('profil.title')).toBe('Profil');
  });

  it('returns the correct string with interpolation', () => {
    const result = i18n.t('dashboard.season_label_level', { level: 3 });
    expect(result).toBe('Saison 1 · niv. 3');
  });

  it('falls back to the key when the key is absent', () => {
    const result = i18n.t('non_existent.key');
    expect(result).toBe('non_existent.key');
  });

  it('returns the correct string for a nested key', () => {
    expect(i18n.t('profil.stats.cab')).toBe('Cab');
  });

  it('returns the correct string for an auth key', () => {
    expect(i18n.t('auth.continue_with_google')).toBe('Continuer avec Google');
  });
});
