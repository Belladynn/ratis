import { render, fireEvent } from "@testing-library/react-native";
import { Linking } from "react-native";
import { LegalFooter } from "@/components/LegalFooter";
import { LEGAL_URLS } from "@/constants/Legal";

describe("<LegalFooter />", () => {
  it("renders CGU and privacy links", () => {
    const { getByTestId } = render(<LegalFooter />);
    expect(getByTestId("legal-cgu")).toBeTruthy();
    expect(getByTestId("legal-privacy")).toBeTruthy();
  });

  it("opens CGU URL on press", () => {
    const spy = jest.spyOn(Linking, "openURL").mockResolvedValue(true);
    const { getByTestId } = render(<LegalFooter />);
    fireEvent.press(getByTestId("legal-cgu"));
    expect(spy).toHaveBeenCalledWith(LEGAL_URLS.cgu);
    spy.mockRestore();
  });

  it("opens privacy URL on press", () => {
    const spy = jest.spyOn(Linking, "openURL").mockResolvedValue(true);
    const { getByTestId } = render(<LegalFooter />);
    fireEvent.press(getByTestId("legal-privacy"));
    expect(spy).toHaveBeenCalledWith(LEGAL_URLS.privacy);
    spy.mockRestore();
  });
});
