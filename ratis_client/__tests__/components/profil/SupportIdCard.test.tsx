import React from "react";
import { render, fireEvent, act, waitFor } from "@testing-library/react-native";

const mockSetStringAsync = jest.fn();
jest.mock("expo-clipboard", () => ({
  setStringAsync: (...args: unknown[]) => mockSetStringAsync(...args),
}));

import { SupportIdCard } from "@/components/profil/SupportIdCard";

beforeEach(() => {
  jest.clearAllMocks();
  mockSetStringAsync.mockResolvedValue(undefined);
});

describe("SupportIdCard", () => {
  it("renders the formatted support_id", () => {
    const { getByTestId } = render(<SupportIdCard support_id="RTS-A3K7XP" />);
    expect(getByTestId("support-id-value").children[0]).toBe("RTS-A3K7XP");
  });

  it("renders title and description from i18n", () => {
    const { getByText } = render(<SupportIdCard support_id="RTS-A3K7XP" />);
    expect(getByText("Mon identifiant support")).toBeTruthy();
    expect(
      getByText(
        "Communique cet identifiant pour toute demande au support.",
      ),
    ).toBeTruthy();
  });

  it("copies the support_id to clipboard on press", async () => {
    const { getByTestId } = render(<SupportIdCard support_id="RTS-COPY01" />);
    await act(async () => {
      fireEvent.press(getByTestId("support-id-copy"));
    });
    expect(mockSetStringAsync).toHaveBeenCalledTimes(1);
    expect(mockSetStringAsync).toHaveBeenCalledWith("RTS-COPY01");
  });

  it("shows the copied toast feedback after copy succeeds", async () => {
    const { getByTestId, queryByTestId } = render(
      <SupportIdCard support_id="RTS-TOAST1" />,
    );
    expect(queryByTestId("support-id-copied-toast")).toBeNull();

    await act(async () => {
      fireEvent.press(getByTestId("support-id-copy"));
    });

    await waitFor(() => {
      expect(getByTestId("support-id-copied-toast")).toBeTruthy();
    });
  });

  it("exposes accessibility props on the copy button", () => {
    const { getByTestId } = render(<SupportIdCard support_id="RTS-A11Y01" />);
    const btn = getByTestId("support-id-copy");
    expect(btn.props.accessibilityRole).toBe("button");
    expect(btn.props.accessibilityLabel).toBeTruthy();
    expect(btn.props.accessibilityHint).toBeTruthy();
  });
});
