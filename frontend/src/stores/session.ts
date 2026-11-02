import { defineStore } from "pinia";
import { ref } from "vue";
import { api, type AuthStatus, type SessionUser } from "@/api";

type SessionRedirect = (reason: "sessionExpired" | "signedOut") => void | Promise<void>;

let redirectHandler: SessionRedirect | null = null;

export function setSessionRedirectHandler(handler: SessionRedirect | null) {
  redirectHandler = handler;
}

export const useSessionStore = defineStore("session", () => {
  const user = ref<SessionUser | null>(null);
  const status = ref<AuthStatus | null>(null);
  const error = ref("");
  const loading = ref(false);
  const ready = ref(false);

  function clearLocalSession() {
    user.value = null;
  }

  async function leaveForLogin(reason: "sessionExpired" | "signedOut") {
    clearLocalSession();
    if (redirectHandler) {
      await redirectHandler(reason);
    }
  }

  /** Called by the API client when a protected request returns 401. */
  function handleUnauthorized() {
    void leaveForLogin("sessionExpired");
  }

  async function refresh() {
    try {
      status.value = await api.authStatus();
    } catch {
      status.value = null;
    }
    const hadUser = Boolean(user.value);
    try {
      user.value = await api.me();
      error.value = "";
    } catch {
      user.value = null;
      if (hadUser) {
        await leaveForLogin("sessionExpired");
      }
    } finally {
      ready.value = true;
    }
  }

  async function signIn(email: string) {
    loading.value = true;
    error.value = "";
    try {
      user.value = await api.login(email);
    } catch (err) {
      user.value = null;
      error.value = err instanceof Error ? err.message : "Sign-in failed.";
    } finally {
      loading.value = false;
    }
  }

  async function signInPassword(username: string, password: string) {
    loading.value = true;
    error.value = "";
    try {
      user.value = await api.passwordLogin(username, password);
    } catch (err) {
      user.value = null;
      error.value = err instanceof Error ? err.message : "Sign-in failed.";
    } finally {
      loading.value = false;
    }
  }

  function signInGoogle() {
    loading.value = true;
    error.value = "";
    api.startGoogle();
  }

  async function signOut() {
    try {
      await api.logout();
    } catch {
      /* already expired cookies are fine */
    }
    await leaveForLogin("signedOut");
  }

  function setError(message: string) {
    error.value = message;
  }

  return {
    user,
    status,
    error,
    loading,
    ready,
    refresh,
    signIn,
    signInPassword,
    signInGoogle,
    signOut,
    setError,
    handleUnauthorized,
    clearLocalSession,
  };
});
