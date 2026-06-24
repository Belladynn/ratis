import { renderHook, waitFor } from "@testing-library/react-native";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { useReferralCode } from "@/hooks/use-referral-code";
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

describe("useReferralCode", () => {
  it("fetches from GET /rewards/referral/code", async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      code: "ABCD1234",
      created_at: "2026-04-22T14:30:00+00:00",
    });

    const { result } = renderHook(() => useReferralCode(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(rewardsClient.get).toHaveBeenCalledWith("/rewards/referral/code");
    expect(result.current.data?.code).toBe("ABCD1234");
  });

  it("exposes loading and error states", async () => {
    (rewardsClient.get as jest.Mock).mockRejectedValue(new Error("network"));

    const { result } = renderHook(() => useReferralCode(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toContain("network");
  });

  it("caches the code across renders (staleTime > 0)", async () => {
    (rewardsClient.get as jest.Mock).mockResolvedValue({
      code: "STABLE01",
      created_at: "2026-04-22T14:30:00+00:00",
    });

    const wrapper = makeWrapper();
    const { result: r1 } = renderHook(() => useReferralCode(), { wrapper });
    await waitFor(() => expect(r1.current.isSuccess).toBe(true));

    // Render again — no new fetch
    const { result: r2 } = renderHook(() => useReferralCode(), { wrapper });
    await waitFor(() => expect(r2.current.isSuccess).toBe(true));

    expect(rewardsClient.get).toHaveBeenCalledTimes(1);
  });
});
