import { renderHook, waitFor } from "@testing-library/react-native";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { useReferralHistory } from "@/hooks/use-referral-history";
import { rewardsClient } from "@/services/rewards-client";

jest.mock("@/services/rewards-client", () => ({
  rewardsClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("useReferralHistory", () => {
  it("fetches from GET /rewards/referral/history", async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      code: "ABCD1234",
      stats: { total_uses: 3, rewarded_uses: 1, total_cab_earned: 500 },
      uses: [
        {
          referred_user_display_name: "Alice",
          plan: "monthly",
          status: "rewarded",
          rewarded_at: "2026-04-15T10:00:00+00:00",
          created_at: "2026-04-01T12:00:00+00:00",
        },
        {
          referred_user_display_name: null,
          plan: null,
          status: "pending",
          rewarded_at: null,
          created_at: "2026-04-20T09:30:00+00:00",
        },
      ],
    });

    const { result } = renderHook(() => useReferralHistory(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(rewardsClient.get).toHaveBeenCalledWith("/rewards/referral/history");
    expect(result.current.data?.code).toBe("ABCD1234");
    expect(result.current.data?.stats.total_uses).toBe(3);
    expect(result.current.data?.uses).toHaveLength(2);
    // RGPD check — display_name may be null (anonymised filleul)
    expect(result.current.data?.uses[1].referred_user_display_name).toBeNull();
  });

  it("exposes loading state before fetch resolves", () => {
    (rewardsClient.get as jest.Mock).mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() => useReferralHistory(), {
      wrapper: makeWrapper(),
    });
    expect(result.current.isLoading).toBe(true);
    expect(result.current.data).toBeUndefined();
  });

  it("exposes error on failed fetch", async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(
      new Error("server_error"),
    );

    const { result } = renderHook(() => useReferralHistory(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
