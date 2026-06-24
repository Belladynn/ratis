// ratis_client/hooks/useAuth.ts

import { useContext } from "react";
import { AuthContext, AuthContextValue } from "@/contexts/AuthContext";

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}
