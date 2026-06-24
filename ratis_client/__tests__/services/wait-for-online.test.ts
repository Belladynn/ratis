// ratis_client/__tests__/services/wait-for-online.test.ts

jest.mock("expo-network");

import { waitForOnline } from "@/services/wait-for-online";
import * as Network from "expo-network";

const mockNet = Network as unknown as {
  __setNetworkState: (s: { isConnected: boolean }) => void;
  __reset: () => void;
};

describe("waitForOnline", () => {
  beforeEach(() => mockNet.__reset());

  it("returns true immediately when already online", async () => {
    mockNet.__setNetworkState({ isConnected: true });
    await expect(waitForOnline(5000)).resolves.toBe(true);
  });

  it("resolves true when network becomes online", async () => {
    mockNet.__setNetworkState({ isConnected: false });
    const promise = waitForOnline(5000);

    setTimeout(() => mockNet.__setNetworkState({ isConnected: true }), 10);

    await expect(promise).resolves.toBe(true);
  });

  it("resolves false after timeout if still offline", async () => {
    mockNet.__setNetworkState({ isConnected: false });
    await expect(waitForOnline(50)).resolves.toBe(false);
  });

  it(
    "resolves only once even with rapid online/offline oscillation",
    async () => {
      mockNet.__setNetworkState({ isConnected: false });
      const promise = waitForOnline(5000);

      // Rapid state changes asynchronously
      setTimeout(() => mockNet.__setNetworkState({ isConnected: true }), 5);
      setTimeout(() => mockNet.__setNetworkState({ isConnected: false }), 10);
      setTimeout(() => mockNet.__setNetworkState({ isConnected: true }), 15);

      await expect(promise).resolves.toBe(true);
    },
    10000
  );
});
