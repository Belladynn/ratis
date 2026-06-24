// ratis_client/services/wait-for-online.ts

import * as Network from "expo-network";

export async function waitForOnline(maxWaitMs: number): Promise<boolean> {
  const current = await Network.getNetworkStateAsync();
  if (current.isConnected) return true;

  return new Promise<boolean>((resolve) => {
    let settled = false;

    const settle = (value: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      sub.remove();
      resolve(value);
    };

    const timer = setTimeout(() => settle(false), maxWaitMs);

    const sub = Network.addNetworkStateListener((state) => {
      if (state.isConnected) settle(true);
    });
  });
}
