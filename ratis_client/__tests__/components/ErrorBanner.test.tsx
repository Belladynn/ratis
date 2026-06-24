import { render } from "@testing-library/react-native";
import { ErrorBanner } from "@/components/ErrorBanner";

describe("<ErrorBanner />", () => {
  it("renders the message", () => {
    const { getByText } = render(<ErrorBanner message="Oops" />);
    expect(getByText("Oops")).toBeTruthy();
  });

  it("returns null when message is empty", () => {
    const { queryByTestId } = render(<ErrorBanner message="" />);
    expect(queryByTestId("error-banner")).toBeNull();
  });
});
