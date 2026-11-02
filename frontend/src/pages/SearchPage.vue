<template>
  <q-page padding>
    <h1 class="text-h5 q-mb-md">{{ t("search.title") }}</h1>
    <div class="row q-col-gutter-sm q-mb-md">
      <div class="col">
        <q-input v-model="query" outlined dense :label="t('search.query')" @keyup.enter="run" />
      </div>
      <div class="col-auto">
        <q-btn color="primary" unelevated :label="t('search.run')" :disable="!session.user" @click="run" />
      </div>
    </div>
    <q-banner v-if="error" class="bg-negative text-white q-mb-md">{{ error }}</q-banner>
    <q-list v-if="results.length" bordered separator>
      <q-item v-for="hit in results" :key="hit.id">
        <q-item-section>
          <q-item-label>{{ hit.title }}</q-item-label>
          <q-item-label caption>{{ hit.snippet }}</q-item-label>
        </q-item-section>
      </q-item>
    </q-list>
    <div v-else-if="ran" class="text-body2">{{ t("search.none") }}</div>
  </q-page>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { api } from "@/api";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const session = useSessionStore();
const query = ref("Laptop");
const results = ref<Array<{ id: string; title: string; snippet: string }>>([]);
const error = ref("");
const ran = ref(false);

async function run() {
  ran.value = true;
  const body = await api.search(query.value);
  results.value = body.results;
  error.value = body.error ?? "";
}
</script>
