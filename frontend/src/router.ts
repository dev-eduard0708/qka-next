import { createRouter, createWebHistory } from "vue-router";
import { useSessionStore } from "@/stores/session";
import MainLayout from "@/layouts/MainLayout.vue";
import LoginLayout from "@/layouts/LoginLayout.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/login",
      component: LoginLayout,
      meta: { public: true },
      children: [{ path: "", name: "login", component: () => import("@/pages/LoginPage.vue") }],
    },
    {
      path: "/",
      component: MainLayout,
      children: [
        { path: "", name: "dashboard", component: () => import("@/pages/DashboardPage.vue") },
        { path: "documents", name: "documents", component: () => import("@/pages/DocumentsPage.vue") },
        { path: "search", name: "search", component: () => import("@/pages/SearchPage.vue") },
        { path: "ai", name: "ai", component: () => import("@/pages/AiWorkspacePage.vue") },
        { path: "access", name: "access", meta: { superAdmin: true }, component: () => import("@/pages/AccessPage.vue") },
        { path: "integrations", name: "integrations", component: () => import("@/pages/IntegrationsPage.vue") },
        {
          path: "integrations/synology",
          name: "synology",
          component: () => import("@/pages/SynologyPage.vue"),
        },
        { path: "settings", name: "settings", component: () => import("@/pages/SettingsPage.vue") },
        { path: "settings/integrations/synology", redirect: { name: "synology" } },
      ],
    },
  ],
});

router.beforeEach(async (to) => {
  const session = useSessionStore();
  if (!session.ready) {
    await session.refresh();
  } else if (!to.meta.public) {
    // Re-check the cookie on every protected navigation so an expired session
    // cannot keep showing a dead page as if the user were still signed in.
    await session.refresh();
  }
  if (to.meta.public) {
    if (session.user) {
      const next = typeof to.query.next === "string" && to.query.next.startsWith("/") ? to.query.next : "/";
      return { path: next };
    }
    return true;
  }
  if (!session.user) {
    return {
      path: "/login",
      query: {
        authError: "sessionExpired",
        ...(to.fullPath && to.fullPath !== "/" ? { next: to.fullPath } : {}),
      },
    };
  }
  if (to.meta.superAdmin && !session.user.superAdmin) {
    return { path: "/" };
  }
  return true;
});
