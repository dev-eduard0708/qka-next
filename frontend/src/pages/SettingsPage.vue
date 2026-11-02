<template>
  <q-page padding>
    <h1 class="text-h5 q-mb-md">{{ t("settings.title") }}</h1>
    <q-list bordered class="q-mb-lg">
      <q-item clickable v-ripple to="/integrations/synology">
        <q-item-section avatar><q-icon name="storage" /></q-item-section>
        <q-item-section>
          <q-item-label>{{ t("synology.title") }}</q-item-label>
          <q-item-label caption>{{ t("settings.synologyHint") }}</q-item-label>
        </q-item-section>
      </q-item>
    </q-list>
    <q-list bordered>
      <q-item>
        <q-item-section>{{ t("settings.theme") }}</q-item-section>
        <q-item-section side>
          <q-btn-toggle
            v-model="mode"
            spread
            unelevated
            toggle-color="primary"
            :options="[
              { label: t('settings.light'), value: 'light' },
              { label: t('settings.dark'), value: 'dark' },
            ]"
            @update:model-value="applyTheme"
          />
        </q-item-section>
      </q-item>
      <q-item>
        <q-item-section>{{ t("settings.language") }}</q-item-section>
        <q-item-section side>
          <q-btn-toggle
            v-model="localeModel"
            unelevated
            toggle-color="secondary"
            :options="[
              { label: 'English', value: 'en' },
              { label: 'العربية', value: 'ar' },
            ]"
            @update:model-value="applyLocale"
          />
        </q-item-section>
      </q-item>
    </q-list>
  </q-page>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { Dark, Quasar } from "quasar";
import { useI18n } from "vue-i18n";
import langAr from "quasar/lang/ar";
import langEn from "quasar/lang/en-US";

const { t, locale } = useI18n();
const mode = ref(Dark.isActive ? "dark" : "light");
const localeModel = ref(locale.value);

function applyTheme(value: string) {
  Dark.set(value === "dark");
}

function applyLocale(value: string) {
  locale.value = value;
  Quasar.lang.set(value === "ar" ? langAr : langEn);
  document.documentElement.dir = value === "ar" ? "rtl" : "ltr";
  document.documentElement.lang = value;
}
</script>
