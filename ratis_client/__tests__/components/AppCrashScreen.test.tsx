// ratis_client/__tests__/components/AppCrashScreen.test.tsx

const mockReload = jest.fn().mockResolvedValue(undefined);
jest.mock("expo-updates", () => ({
  reloadAsync: () => mockReload(),
}));

import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import "@/lib/i18n";
import { AppCrashScreen } from "@/components/AppCrashScreen";

describe("<AppCrashScreen />", () => {
  beforeEach(() => mockReload.mockClear());

  it("renders a recovery button", () => {
    const { getByTestId } = render(<AppCrashScreen />);
    expect(getByTestId("app-crash-reload")).toBeTruthy();
  });

  it("calls Updates.reloadAsync when the recovery button is pressed", () => {
    const { getByTestId } = render(<AppCrashScreen />);
    fireEvent.press(getByTestId("app-crash-reload"));
    expect(mockReload).toHaveBeenCalledTimes(1);
  });

  it("renders translated copy, not hardcoded strings", () => {
    const { getByText } = render(<AppCrashScreen />);
    // Keys come from locales/fr.json crash.* — assert the resolved text.
    expect(getByText("Oups")).toBeTruthy();
    expect(getByText("Recharger l'application")).toBeTruthy();
  });
});
