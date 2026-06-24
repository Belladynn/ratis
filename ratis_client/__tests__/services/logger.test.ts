// ratis_client/__tests__/services/logger.test.ts

import { captureException } from "@sentry/react-native";
import { logger, __internal__ } from "@/services/logger";

describe("logger.sanitize", () => {
  const { sanitize } = __internal__;

  it("returns undefined unchanged", () => {
    expect(sanitize(undefined)).toBeUndefined();
  });

  it("strips access_token", () => {
    expect(sanitize({ access_token: "secret", ok: 1 })).toEqual({ ok: 1 });
  });

  it("strips refresh_token", () => {
    expect(sanitize({ refresh_token: "s", foo: "bar" })).toEqual({ foo: "bar" });
  });

  it("strips nested PII — email, display_name, password, token, idToken", () => {
    const input = {
      email: "x@y.z",
      display_name: "Foo",
      password: "pw", // pragma: allowlist secret
      token: "t",
      idToken: "id",
      keep: "yes",
    };
    expect(sanitize(input)).toEqual({ keep: "yes" });
  });

  it("does not mutate the input", () => {
    const input = { access_token: "secret" };
    sanitize(input);
    expect(input).toEqual({ access_token: "secret" });
  });
});

describe("logger methods", () => {
  let logSpy: jest.SpyInstance;
  let warnSpy: jest.SpyInstance;
  let errorSpy: jest.SpyInstance;

  beforeEach(() => {
    logSpy = jest.spyOn(console, "log").mockImplementation();
    warnSpy = jest.spyOn(console, "warn").mockImplementation();
    errorSpy = jest.spyOn(console, "error").mockImplementation();
    jest.clearAllMocks();
  });

  afterEach(() => {
    logSpy.mockRestore();
    warnSpy.mockRestore();
    errorSpy.mockRestore();
  });

  it("info prefixes [INFO] and scrubs PII", () => {
    logger.info("auth.signin", { access_token: "secret", provider: "google" });
    expect(logSpy).toHaveBeenCalledWith("[INFO] auth.signin", { provider: "google" });
  });

  it("warn prefixes [WARN] and scrubs PII", () => {
    logger.warn("auth.slow", { email: "x@y.z", duration: 900 });
    expect(warnSpy).toHaveBeenCalledWith("[WARN] auth.slow", { duration: 900 });
  });

  it("error prefixes [ERROR] and scrubs PII", () => {
    const err = new Error("boom");
    logger.error("auth.fail", err, { refresh_token: "r", code: "timeout" });
    expect(errorSpy).toHaveBeenCalledWith("[ERROR] auth.fail", err, { code: "timeout" });
  });

  it("error forwards to Sentry.captureException with scrubbed extra", () => {
    const err = new Error("crash");
    logger.error("auth.crash", err, { access_token: "secret", code: "503" });
    expect(captureException).toHaveBeenCalledWith(err, {
      extra: { code: "503" },
    });
  });

  it("error wraps non-Error in a new Error before sending to Sentry", () => {
    logger.error("auth.string_error", "something went wrong");
    expect(captureException).toHaveBeenCalledWith(
      expect.objectContaining({ message: "something went wrong" }),
      { extra: undefined },
    );
  });
});
