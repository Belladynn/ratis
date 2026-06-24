import React from "react";
import { render, fireEvent, act } from "@testing-library/react-native";
import { Share } from "react-native";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("@/components/ui/screen-background-legacy", () => ({
  ScreenBackground: () => null,
}));
jest.mock("react-native-safe-area-context", () => ({
  SafeAreaView: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock("expo-router", () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn() }),
}));

const mockShare = jest
  .spyOn(Share, "share")
  .mockImplementation(
    async () =>
      ({ action: "sharedAction" }) as Awaited<ReturnType<typeof Share.share>>,
  );

const mockSetStringAsync = jest.fn();
jest.mock("expo-clipboard", () => ({
  setStringAsync: (...args: unknown[]) => mockSetStringAsync(...args),
}));

const mockCodeState = {
  data: undefined as { code: string; created_at: string } | undefined,
  isLoading: false,
  isError: false,
  error: null as Error | null,
  refetch: jest.fn(),
};
const mockHistoryState = {
  data: undefined as unknown,
  isLoading: false,
  isError: false,
  error: null as Error | null,
  refetch: jest.fn(),
};
jest.mock("@/hooks/use-referral-code", () => ({
  useReferralCode: () => mockCodeState,
}));
jest.mock("@/hooks/use-referral-history", () => ({
  useReferralHistory: () => mockHistoryState,
}));

import ReferralScreen from "@/app/referral";

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function resetMocks() {
  jest.clearAllMocks();
  mockCodeState.data = undefined;
  mockCodeState.isLoading = false;
  mockCodeState.isError = false;
  mockCodeState.error = null;
  mockHistoryState.data = undefined;
  mockHistoryState.isLoading = false;
  mockHistoryState.isError = false;
  mockHistoryState.error = null;
}

beforeEach(resetMocks);

describe("ReferralScreen", () => {
  it("renders loading state while code is fetching", () => {
    mockCodeState.isLoading = true;
    const { getByTestId } = renderWithQuery(<ReferralScreen />);
    expect(getByTestId("referral-loading")).toBeTruthy();
  });

  it("renders error state with retry button when code fetch fails", () => {
    mockCodeState.isError = true;
    mockCodeState.error = new Error("server_down");
    const { getByTestId } = renderWithQuery(<ReferralScreen />);
    expect(getByTestId("referral-error")).toBeTruthy();
    fireEvent.press(getByTestId("referral-error-retry"));
    expect(mockCodeState.refetch).toHaveBeenCalledTimes(1);
  });

  it("renders the referral code when loaded", () => {
    mockCodeState.data = {
      code: "ABCD1234",
      created_at: "2026-04-22T14:30:00+00:00",
    };
    const { getByTestId } = renderWithQuery(<ReferralScreen />);
    expect(getByTestId("referral-code-value").children[0]).toBe("ABCD1234");
  });

  it("copies the code to clipboard on press", async () => {
    mockCodeState.data = {
      code: "COPYME01",
      created_at: "2026-04-22T14:30:00+00:00",
    };
    mockSetStringAsync.mockResolvedValue(undefined);

    const { getByTestId } = renderWithQuery(<ReferralScreen />);
    await act(async () => {
      fireEvent.press(getByTestId("referral-copy"));
    });

    expect(mockSetStringAsync).toHaveBeenCalledWith("COPYME01");
  });

  it("opens the share sheet with the code embedded in the message", async () => {
    mockCodeState.data = {
      code: "SHARE123",
      created_at: "2026-04-22T14:30:00+00:00",
    };
    mockShare.mockResolvedValue({ action: "sharedAction" });

    const { getByTestId } = renderWithQuery(<ReferralScreen />);
    await act(async () => {
      fireEvent.press(getByTestId("referral-share"));
    });

    expect(mockShare).toHaveBeenCalledTimes(1);
    const [payload] = mockShare.mock.calls[0];
    expect(payload.message).toContain("SHARE123");
  });

  it("renders stats tiles from history", () => {
    mockCodeState.data = {
      code: "STATS001",
      created_at: "2026-04-22T14:30:00+00:00",
    };
    mockHistoryState.data = {
      code: "STATS001",
      stats: { total_uses: 12, rewarded_uses: 4, total_cab_earned: 2500 },
      uses: [],
    };
    const { getByTestId } = renderWithQuery(<ReferralScreen />);
    expect(getByTestId("referral-stat-signups").children[0]).toBe("12");
    expect(getByTestId("referral-stat-subscribers").children[0]).toBe("4");
    expect(getByTestId("referral-stat-cab-earned").children[0]).toBe("2500");
  });

  it("renders empty state in history when no filleuls", () => {
    mockCodeState.data = {
      code: "EMPTY001",
      created_at: "2026-04-22T14:30:00+00:00",
    };
    mockHistoryState.data = {
      code: "EMPTY001",
      stats: { total_uses: 0, rewarded_uses: 0, total_cab_earned: 0 },
      uses: [],
    };
    const { getByTestId } = renderWithQuery(<ReferralScreen />);
    expect(getByTestId("referral-history-empty")).toBeTruthy();
  });

  it("renders each filleul with display_name + status", () => {
    mockCodeState.data = {
      code: "LIST0001",
      created_at: "2026-04-22T14:30:00+00:00",
    };
    mockHistoryState.data = {
      code: "LIST0001",
      stats: { total_uses: 2, rewarded_uses: 1, total_cab_earned: 500 },
      uses: [
        {
          referred_user_display_name: "Alice",
          plan: "monthly",
          status: "rewarded",
          rewarded_at: "2026-04-10T09:00:00+00:00",
          created_at: "2026-04-01T12:00:00+00:00",
        },
        {
          referred_user_display_name: null,
          plan: null,
          status: "pending",
          rewarded_at: null,
          created_at: "2026-04-15T18:30:00+00:00",
        },
      ],
    };
    const { getByText, queryByText } = renderWithQuery(<ReferralScreen />);
    expect(getByText("Alice")).toBeTruthy();
    // Second row: display_name is null → fallback label "Nouveau filleul"
    expect(getByText("Nouveau filleul")).toBeTruthy();
    // Status labels visible
    expect(queryByText("Abonné mensuel")).toBeTruthy();
    expect(queryByText("Inscrit")).toBeTruthy();
  });

  it("does not leak email in the rendered output (RGPD check)", () => {
    mockCodeState.data = {
      code: "PRIV0001",
      created_at: "2026-04-22T14:30:00+00:00",
    };
    mockHistoryState.data = {
      code: "PRIV0001",
      stats: { total_uses: 1, rewarded_uses: 1, total_cab_earned: 500 },
      uses: [
        {
          referred_user_display_name: "Bob",
          plan: "monthly",
          status: "rewarded",
          rewarded_at: "2026-04-10T09:00:00+00:00",
          created_at: "2026-04-01T12:00:00+00:00",
        },
      ],
    };
    const { toJSON } = renderWithQuery(<ReferralScreen />);
    const tree = JSON.stringify(toJSON());
    // Never render anything that looks like an email
    expect(tree).not.toMatch(/@[\w.]+\.[a-z]{2,}/i);
    // UUIDs either
    expect(tree).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/,
    );
  });
});
