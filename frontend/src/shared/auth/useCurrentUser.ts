import { useAuthStore } from "./authStore";

export function useCurrentUser() {
  return useAuthStore().currentUser;
}
