// Mock the Google Fonts package + expo-font BEFORE importing the hook so
// that the import-time map of weights is captured by our test spy.

type UseFontsReturn = [boolean, Error | null];
const mockUseFonts = jest.fn<UseFontsReturn, [Record<string, unknown>]>(() => [
  false,
  null,
]);

jest.mock('@expo-google-fonts/inter', () => ({
  __esModule: true,
  Inter_400Regular: 'Inter_400Regular_TOKEN',
  Inter_500Medium: 'Inter_500Medium_TOKEN',
  Inter_600SemiBold: 'Inter_600SemiBold_TOKEN',
  Inter_700Bold: 'Inter_700Bold_TOKEN',
  Inter_800ExtraBold: 'Inter_800ExtraBold_TOKEN',
  Inter_900Black: 'Inter_900Black_TOKEN',
  useFonts: (map: Record<string, unknown>) => mockUseFonts(map),
}));

import { useDesignSystemFonts, INTER_WEIGHTS } from '@/hooks/use-fonts';

describe('useDesignSystemFonts', () => {
  beforeEach(() => {
    mockUseFonts.mockClear();
  });

  it('exports the 6 Inter weights consumed by the design system', () => {
    expect(INTER_WEIGHTS).toEqual([
      'Inter_400Regular',
      'Inter_500Medium',
      'Inter_600SemiBold',
      'Inter_700Bold',
      'Inter_800ExtraBold',
      'Inter_900Black',
    ]);
  });

  it('forwards every weight to expo-font useFonts when invoked', () => {
    useDesignSystemFonts();

    expect(mockUseFonts).toHaveBeenCalledTimes(1);
    const call = mockUseFonts.mock.calls[0];
    expect(call).toBeDefined();
    const map = call![0];

    expect(Object.keys(map).sort()).toEqual([...INTER_WEIGHTS].sort());
    expect(map.Inter_400Regular).toBe('Inter_400Regular_TOKEN');
    expect(map.Inter_900Black).toBe('Inter_900Black_TOKEN');
  });

  it('returns the loaded boolean from useFonts', () => {
    mockUseFonts.mockReturnValueOnce([true, null]);
    const [loaded, error] = useDesignSystemFonts();
    expect(loaded).toBe(true);
    expect(error).toBeNull();
  });

  it('surfaces useFonts errors verbatim', () => {
    const err = new Error('boom');
    mockUseFonts.mockReturnValueOnce([false, err]);
    const [loaded, error] = useDesignSystemFonts();
    expect(loaded).toBe(false);
    expect(error).toBe(err);
  });
});
