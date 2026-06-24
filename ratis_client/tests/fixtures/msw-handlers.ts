// ratis_client/tests/fixtures/msw-handlers.ts

import { http, HttpResponse } from "msw";

export const BASE_URL = "https://api.test.ratis.app/api/v1";

export const defaultHandlers = [
  http.get(`${BASE_URL}/auth/me`, () => {
    return HttpResponse.json({
      id: "u-1",
      email: "test@ratis.app",
      display_name: "Tester",
      avatar_url: null,
      provider: "google",
      timezone: "Europe/Paris",
      current_level_id: null,
    });
  }),

  http.post(`${BASE_URL}/auth/oauth`, () => {
    return HttpResponse.json({
      access_token: "access.jwt.1",
      refresh_token: "refresh.jwt.1",
      expires_in: 900,
      token_type: "bearer",
    });
  }),

  http.post(`${BASE_URL}/auth/refresh`, () => {
    return HttpResponse.json({
      access_token: "access.jwt.2",
      refresh_token: "refresh.jwt.2",
      expires_in: 900,
      token_type: "bearer",
    });
  }),

  http.post(`${BASE_URL}/account/logout`, () => {
    return HttpResponse.json({ ok: true });
  }),
];
