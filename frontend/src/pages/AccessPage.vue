<template>
  <q-page padding>
    <h1 class="text-h5 q-mb-sm">{{ t("access.title") }}</h1>
    <p class="text-body2 q-mb-lg">{{ t("access.body") }}</p>

    <q-card v-if="session.user?.superAdmin" bordered>
      <q-card-section>
        <div class="text-subtitle1 q-mb-sm">{{ t("access.allowlist") }}</div>
        <p class="text-caption q-mb-md">{{ t("access.domainHint") }}</p>
        <q-banner v-if="error" dense class="bg-negative text-white q-mb-md">{{ error }}</q-banner>
        <q-form class="row q-col-gutter-sm q-mb-md" @submit.prevent="add">
          <div class="col">
            <q-input v-model="email" outlined dense :label="t('access.email')" type="email" />
          </div>
          <div class="col-auto">
            <q-btn color="primary" unelevated type="submit" :loading="adding" :label="t('access.add')" />
          </div>
        </q-form>
        <q-list v-if="rows.length" bordered separator>
          <q-item v-for="row in rows" :key="row.id">
            <q-item-section>
              <q-item-label>{{ row.email }}</q-item-label>
              <q-item-label caption>{{ row.enabled ? t("access.enabled") : t("access.disabled") }}</q-item-label>
            </q-item-section>
            <q-item-section side>
              <div class="row items-center no-wrap q-gutter-sm">
                <q-toggle
                  :model-value="row.enabled"
                  :disable="busyId === row.id"
                  @update:model-value="(value: boolean) => toggle(row, value)"
                />
                <q-btn
                  flat
                  dense
                  color="negative"
                  :label="t('access.remove')"
                  :loading="busyId === row.id"
                  @click="remove(row.id)"
                />
              </div>
            </q-item-section>
          </q-item>
        </q-list>
        <div v-else class="text-body2">{{ t("access.empty") }}</div>
      </q-card-section>
    </q-card>
  </q-page>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api, type AllowedEmail } from "@/api";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const session = useSessionStore();
const rows = ref<AllowedEmail[]>([]);
const email = ref("");
const error = ref("");
const adding = ref(false);
const busyId = ref<string | null>(null);

async function load() {
  if (!session.user?.superAdmin) {
    rows.value = [];
    return;
  }
  try {
    rows.value = await api.listAllowedEmails();
    error.value = "";
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("access.loadError");
  }
}

async function add() {
  adding.value = true;
  error.value = "";
  try {
    const created = await api.addAllowedEmail(email.value);
    rows.value = [...rows.value.filter((row) => row.id !== created.id), created].sort((a, b) =>
      a.email.localeCompare(b.email),
    );
    email.value = "";
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("access.addError");
  } finally {
    adding.value = false;
  }
}

async function toggle(row: AllowedEmail, enabled: boolean) {
  busyId.value = row.id;
  try {
    const updated = await api.setAllowedEmailEnabled(row.id, enabled);
    rows.value = rows.value.map((item) => (item.id === updated.id ? updated : item));
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("access.updateError");
  } finally {
    busyId.value = null;
  }
}

async function remove(id: string) {
  busyId.value = id;
  try {
    await api.removeAllowedEmail(id);
    rows.value = rows.value.filter((row) => row.id !== id);
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("access.removeError");
  } finally {
    busyId.value = null;
  }
}

onMounted(load);
</script>
