import { render, fireEvent } from "@testing-library/react-native";
import { GoogleButton } from "@/components/GoogleButton";

describe("<GoogleButton />", () => {
  it("fires onPress when not disabled", () => {
    const onPress = jest.fn();
    const { getByTestId } = render(<GoogleButton onPress={onPress} />);
    fireEvent.press(getByTestId("google-signin"));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it("does not fire onPress when disabled", () => {
    const onPress = jest.fn();
    const { getByTestId } = render(<GoogleButton onPress={onPress} disabled />);
    fireEvent.press(getByTestId("google-signin"));
    expect(onPress).not.toHaveBeenCalled();
  });

  it("shows loading state", () => {
    const { getByTestId } = render(<GoogleButton onPress={() => {}} loading />);
    expect(getByTestId("google-signin-spinner")).toBeTruthy();
  });
});
