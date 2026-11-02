<template>
  <q-page class="flex flex-center q-pa-md">
    <q-card bordered class="qka-login-card">
      <q-card-section>
        <h1 class="text-h5 q-mb-sm">{{ t("login.title") }}</h1>
        <p class="text-body2 q-mb-md">{{ t("session.hint") }}</p>
        <q-banner v-if="displayError" dense class="bg-negative text-white q-mb-md">{{ displayError }}</q-banner>
        <q-form @submit.prevent="signInPassword">
          <q-input
            v-model="username"
            :label="t('session.username')"
            outlined
            dense
            autocomplete="username"
            class="q-mb-sm"
          />
          <q-input
            v-model="password"
            :label="t('session.password')"
            type="password"
            outlined
            dense
            autocomplete="current-password"
            class="q-mb-md"
          />
          <q-btn
            type="submit"
            color="primary"
            unelevated
            class="full-width"
            :loading="session.loading"
            :label="t('session.signIn')"
          />
        </q-form>
        <div class="text-center text-caption q-my-md">{{ t("login.or") }}</div>
        <q-btn
          outline
          color="primary"
          class="full-width q-mb-sm"
          :loading="session.loading"
          :disable="!session.status?.googleConfigured"
          :label="t('session.google')"
          @click="session.signInGoogle()"
        />
        <p v-if="session.status && !session.status.googleConfigured" class="text-caption q-mb-sm">
          {{ t("session.googleUnavailable") }}
        </p>
        <p class="text-caption">{{ t("session.pendingLink") }}</p>
      </q-card-section>
    </q-card>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const session = useSessionStore();
const username = ref("");
const password = ref("");

const displayError = computed(() => {
  const raw = session.error;
  if (!raw) return "";
  if (raw === "sessionExpired") return t("session.expired");
  if (raw === "signedOut") return "";
  return raw;
});

async function signInPassword() {
  await session.signInPassword(username.value, password.value);
  if (session.user) {
    password.value = "";
    const next = typeof route.query.next === "string" && route.query.next.startsWith("/") ? route.query.next : "/";
    await router.push(next);
  }
}

function messageForAuthError(code: string): string {
  if (code === "sessionExpired") return "sessionExpired";
  if (code === "signedOut") return "";
  return code;
}

onMounted(() => {
  const authError = route.query.authError;
  if (typeof authError === "string" && authError) {
    const message = messageForAuthError(authError);
    if (message) session.setError(message);
    const next = typeof route.query.next === "string" ? route.query.next : undefined;
    void router.replace({ path: "/login", query: next ? { next } : {} });
  }
});
</script>
