<template>
  <q-page padding>
    <h1 class="text-h5 q-mb-md">{{ t("integrations.title") }}</h1>
    <p class="text-body1 q-mb-lg">{{ t("integrations.body") }}</p>
    <q-card bordered>
      <q-card-section>
        <div class="text-subtitle1">{{ t("synology.title") }}</div>
        <p class="text-body2">{{ t("integrations.synologyHint") }}</p>
        <p class="text-caption">{{ t("synology.connectionsHint") }}</p>
      </q-card-section>
      <q-card-actions align="right">
        <q-btn color="primary" unelevated to="/integrations/synology" :label="t('integrations.openSynology')" />
      </q-card-actions>
    </q-card>
  </q-page>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
const { t } = useI18n();
</script>
