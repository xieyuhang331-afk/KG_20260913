import { useSyncExternalStore } from "react";
import type { UserRole } from "@/shared/constants/roles";

export interface CurrentUser {
  id: number;
  role: UserRole;
  tenant_id?: number | null;
  org_id?: number | null;
  province?: string | null;
  city?: string | null;
}

interface AuthSnapshot {
  currentUser: CurrentUser | null;
  bootstrapped: boolean;
}

let snapshot: AuthSnapshot = {
  currentUser: null,
  bootstrapped: false
};

const listeners = new Set<() => void>();

export function getAuthSnapshot(): AuthSnapshot {
  return snapshot;
}

export function setCurrentUser(currentUser: CurrentUser | null): void {
  snapshot = {
    currentUser,
    bootstrapped: true
  };
  emit();
}

export function markAuthBootstrapped(): void {
  snapshot = {
    ...snapshot,
    bootstrapped: true
  };
  emit();
}

export function useAuthStore(): AuthSnapshot {
  return useSyncExternalStore(subscribe, getAuthSnapshot, getAuthSnapshot);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit(): void {
  listeners.forEach((listener) => listener());
}
