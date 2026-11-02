<template>
  <q-page padding class="qka-ai-page">
    <div class="row items-start q-col-gutter-md">
      <div class="col-12 col-md-3">
        <div class="row items-center q-mb-sm">
          <div class="col text-h5">{{ t("ai.title") }}</div>
        </div>
        <q-btn
          class="full-width q-mb-sm"
          color="primary"
          outline
          icon="add"
          :label="t('ai.newChat')"
          @click="onNew"
        />
        <q-list bordered separator class="qka-ai-history">
          <q-item v-if="!conversations.length">
            <q-item-section class="text-caption">{{ t("ai.noHistory") }}</q-item-section>
          </q-item>
          <q-item
            v-for="item in conversations"
            :key="item.id"
            clickable
            v-ripple
            :active="activeId === item.id"
            @click="open(item.id)"
          >
            <q-item-section>
              <q-item-label>{{ item.title }}</q-item-label>
              <q-item-label caption>{{ item.updatedAt || "" }}</q-item-label>
            </q-item-section>
            <q-item-section side>
              <q-btn flat dense round icon="delete" @click.stop="remove(item.id)" />
            </q-item-section>
          </q-item>
        </q-list>

        <q-card v-if="session.user?.superAdmin" bordered flat class="q-mt-md">
          <q-card-section>
            <div class="text-subtitle2 q-mb-sm">{{ t("ai.sampleUpload") }}</div>
            <div class="text-caption q-mb-sm">{{ t("ai.sampleHint") }}</div>
            <q-file
              v-model="sampleFile"
              dense
              outlined
              accept=".pdf,.xlsx,.xlsm,.csv,.txt,.md,.docx"
              :label="t('ai.chooseFile')"
            />
            <q-btn
              class="q-mt-sm"
              color="primary"
              :disable="!sampleFile"
              :loading="uploading"
              :label="t('ai.uploadAnalyze')"
              @click="uploadSample"
            />
            <div v-if="sampleId" class="text-caption q-mt-sm">
              {{ t("ai.sampleReady") }}: {{ sampleName }}
            </div>
            <div v-if="uploadError" class="text-negative text-caption q-mt-xs">{{ uploadError }}</div>
          </q-card-section>
        </q-card>
      </div>

      <div class="col-12 col-md-9">
        <AiChatPanel
          ref="panel"
          :title="t('ai.chat')"
          :sample-ids="sampleId ? [sampleId] : []"
          :attachment-labels="attachmentLabels"
          @remove-attachment="clearSample"
        />
      </div>
    </div>
  </q-page>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import AiChatPanel from "@/components/AiChatPanel.vue";
import { api, type AiConversationSummary } from "@/api";
import { useSessionStore } from "@/stores/session";

const { t } = useI18n();
const session = useSessionStore();
const conversations = ref<AiConversationSummary[]>([]);
const activeId = ref<string | null>(null);
const panel = ref<InstanceType<typeof AiChatPanel> | null>(null);
const sampleFile = ref<File | null>(null);
const sampleId = ref<string | null>(null);
const sampleName = ref("");
const uploading = ref(false);
const uploadError = ref("");

const attachmentLabels = computed(() =>
  sampleId.value
    ? [{ key: sampleId.value, label: sampleName.value || sampleId.value, kind: "sample" as const, id: sampleId.value }]
    : [],
);

async function loadHistory() {
  if (!session.user) {
    conversations.value = [];
    return;
  }
  const result = await api.aiConversations();
  conversations.value = result.items;
}

function onNew() {
  activeId.value = null;
  panel.value?.newChat();
}

async function open(id: string) {
  activeId.value = id;
  await panel.value?.openConversation(id);
}

async function remove(id: string) {
  await api.aiDeleteConversation(id);
  if (activeId.value === id) onNew();
  await loadHistory();
}

function clearSample() {
  sampleId.value = null;
  sampleName.value = "";
  sampleFile.value = null;
}

async function uploadSample() {
  if (!sampleFile.value) return;
  uploading.value = true;
  uploadError.value = "";
  try {
    const result = await api.aiSampleUpload(sampleFile.value);
    if (!result.ok || !result.id) {
      uploadError.value = result.error || t("ai.uploadFail");
      return;
    }
    sampleId.value = result.id;
    sampleName.value = result.fileName || sampleFile.value.name;
  } catch (err) {
    uploadError.value = err instanceof Error ? err.message : t("ai.uploadFail");
  } finally {
    uploading.value = false;
  }
}

onMounted(loadHistory);
watch(() => session.user, loadHistory);
</script>

<style scoped>
.qka-ai-history {
  max-height: 420px;
  overflow: auto;
}
</style>
