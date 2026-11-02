<template>
  <q-layout view="hHh lpR fFf" class="qka-shell">
    <q-header elevated class="bg-primary text-white">
      <q-toolbar>
        <q-toolbar-title>
          <div class="text-subtitle1">{{ t("app.title") }}</div>
          <div class="text-caption text-white-50">{{ t("app.subtitle") }}</div>
        </q-toolbar-title>
        <q-btn
          flat
          round
          :icon="dark ? 'light_mode' : 'dark_mode'"
          :aria-label="dark ? 'Switch to light mode' : 'Switch to dark mode'"
          @click="toggleDark"
        />
      </q-toolbar>
    </q-header>
    <q-page-container>
      <router-view />
    </q-page-container>
  </q-layout>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { Dark } from "quasar";
import { useI18n } from "vue-i18n";

const { t } = useI18n();
const dark = ref(Dark.isActive);

function toggleDark() {
  Dark.toggle();
  dark.value = Dark.isActive;
}
</script>
