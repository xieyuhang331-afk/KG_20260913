import { fetchMe } from "@/domains/auth/api";
import { getAccessToken } from "@/shared/auth/tokenStorage";
import { markAuthBootstrapped, setCurrentUser } from "@/shared/auth/authStore";

export async function bootstrapAuth(): Promise<void> {
  if (!getAccessToken()) {
    markAuthBootstrapped();
    return;
  }

  try {
    const currentUser = await fetchMe();
    setCurrentUser(currentUser);
  } catch {
    setCurrentUser(null);
  }
}
