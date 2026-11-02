<template>
  <q-layout view="hHh lpR fFf" class="qka-shell">
    <q-header elevated class="bg-primary text-white">
      <q-toolbar>
        <q-btn dense flat round icon="menu" aria-label="Open navigation" @click="drawer = !drawer" />
        <q-toolbar-title>
          <div class="text-subtitle1">{{ t("app.title") }}</div>
          <div class="text-caption text-white-50">{{ t("app.subtitle") }}</div>
        </q-toolbar-title>
        <q-space />
        <q-btn
          flat
          round
          :icon="dark ? 'light_mode' : 'dark_mode'"
          :aria-label="dark ? 'Switch to light mode' : 'Switch to dark mode'"
          @click="toggleDark"
        />
        <q-btn
          v-if="session.user"
          flat
          round
          dense
          class="q-ml-xs"
          :aria-label="t('session.account')"
        >
          <q-avatar size="32px" color="white" text-color="primary" font-size="14px">
            {{ userInitials }}
          </q-avatar>
          <q-menu anchor="bottom right" self="top right">
            <q-list style="min-width: 200px">
              <q-item>
                <q-item-section>
                  <q-item-label>{{ session.user.displayName }}</q-item-label>
                  <q-item-label caption>{{ t("session.account") }}</q-item-label>
                </q-item-section>
              </q-item>
              <q-separator />
              <q-item v-close-popup clickable @click="signOut">
                <q-item-section avatar>
                  <q-icon name="logout" />
                </q-item-section>
                <q-item-section>{{ t("session.signOut") }}</q-item-section>
              </q-item>
            </q-list>
          </q-menu>
        </q-btn>
      </q-toolbar>
    </q-header>

    <q-drawer v-model="drawer" show-if-above bordered :width="270">
      <q-list padding>
        <q-item
          v-for="item in nav"
          :key="item.to"
          clickable
          v-ripple
          :to="item.to"
          :active="item.to === '/' ? route.path === '/' : route.path === item.to || route.path.startsWith(`${item.to}/`)"
          active-class="bg-primary text-white"
        >
          <q-item-section avatar><q-icon :name="item.icon" /></q-item-section>
          <q-item-section>{{ t(item.label) }}</q-item-section>
        </q-item>
      </q-list>
    </q-drawer>

    <q-page-container>
      <router-view />
    </q-page-container>
  </q-layout>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { Dark } from "quasar";
import { useI18n } from "vue-i18n";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const route = useRoute();
const session = useSessionStore();
const drawer = ref(true);
const dark = ref(Dark.isActive);

const nav = computed(() => {
  const items = [
    { to: "/", icon: "dashboard", label: "nav.dashboard" },
    { to: "/documents", icon: "folder", label: "nav.documents" },
    { to: "/search", icon: "search", label: "nav.search" },
    { to: "/ai", icon: "psychology", label: "nav.ai" },
  ];
  if (session.user?.superAdmin) {
    items.push({ to: "/access", icon: "admin_panel_settings", label: "nav.access" });
  }
  items.push(
    { to: "/integrations", icon: "hub", label: "nav.integrations" },
    { to: "/settings", icon: "settings", label: "nav.settings" },
  );
  return items;
});

const userInitials = computed(() => {
  const parts = (session.user?.displayName ?? "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
});

function toggleDark() {
  Dark.toggle();
  dark.value = Dark.isActive;
}

async function signOut() {
  await session.signOut();
}
</script>
