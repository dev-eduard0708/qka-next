import { createApp } from "vue";
import { createPinia } from "pinia";
import { Quasar, Dark, Dialog, Notify } from "quasar";
import iconSet from "quasar/icon-set/material-icons";
import "@quasar/extras/material-icons/material-icons.css";
import "quasar/src/css/index.sass";
import "@/css/app.sass";
import App from "@/App.vue";
import { setUnauthorizedHandler } from "@/api";
import { i18n } from "@/i18n";
import { router } from "@/router";
import { setSessionRedirectHandler, useSessionStore } from "@/stores/session";

const app = createApp(App);
app.config.errorHandler = (err, _instance, info) => {
  console.error("QKA-Next render error", info, err);
};
const pinia = createPinia();
app.use(pinia);
app.use(router);
app.use(i18n);
app.use(Quasar, {
  plugins: { Dark, Dialog, Notify },
  iconSet,
});

const session = useSessionStore(pinia);
let redirectingToLogin = false;

setUnauthorizedHandler(() => session.handleUnauthorized());
setSessionRedirectHandler(async (reason) => {
  if (redirectingToLogin) return;
  const current = router.currentRoute.value;
  if (current.path === "/login") {
    if (reason === "sessionExpired" && !session.error) {
      session.setError("sessionExpired");
    }
    return;
  }
  redirectingToLogin = true;
  try {
    await router.replace({
      path: "/login",
      query: {
        authError: reason,
        ...(current.fullPath && current.fullPath !== "/" ? { next: current.fullPath } : {}),
      },
    });
  } finally {
    redirectingToLogin = false;
  }
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (!session.user) return;
  void session.refresh();
});

app.mount("#app");
