// ratis_client/__mocks__/expo-apple-authentication.tsx

import React from "react";
import { View } from "react-native";

export enum AppleAuthenticationScope {
  EMAIL = "EMAIL",
  FULL_NAME = "FULL_NAME",
}

export enum AppleAuthenticationButtonType {
  SIGN_IN = "SIGN_IN",
  CONTINUE = "CONTINUE",
  SIGN_UP = "SIGN_UP",
}

export enum AppleAuthenticationButtonStyle {
  WHITE = "WHITE",
  WHITE_OUTLINE = "WHITE_OUTLINE",
  BLACK = "BLACK",
}

let available = true;
let nextResult: { identityToken: string; email?: string; fullName?: { givenName?: string; familyName?: string } } | Error = {
  identityToken: "fake.apple.jwt",
  email: "test@privaterelay.appleid.com",
};

export async function isAvailableAsync(): Promise<boolean> {
  return available;
}

export async function signInAsync(_opts: unknown): Promise<{
  identityToken: string;
  email?: string;
  fullName?: { givenName?: string; familyName?: string } | null;
}> {
  if (nextResult instanceof Error) throw nextResult;
  return {
    identityToken: nextResult.identityToken,
    email: nextResult.email,
    fullName: nextResult.fullName ?? null,
  };
}

type ButtonProps = {
  testID?: string;
  onPress?: () => void;
  style?: unknown;
};

export const AppleAuthenticationButton = (props: ButtonProps) => (
  <View testID={props.testID} />
);

// Test helpers
export function __setAvailable(v: boolean) { available = v; }
export function __setNextResult(r: typeof nextResult) { nextResult = r; }
export function __reset() {
  available = true;
  nextResult = {
    identityToken: "fake.apple.jwt",
    email: "test@privaterelay.appleid.com",
  };
}
