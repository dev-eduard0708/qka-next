<template>
  <div class="qka-ai-chat column full-height">
    <div class="row items-center q-gutter-sm q-mb-sm">
      <div class="col">
        <div class="text-subtitle2">{{ title }}</div>
        <div class="text-caption">
          <template v-if="!statusLoaded">…</template>
          <template v-else-if="!aiStatus?.configured">{{ t("ai.notConfigured") }}</template>
          <template v-else>
            {{ t("ai.model") }}: {{ aiStatus.model }}
            <span v-if="aiStatus.dataSharingPermitted"> · {{ t("ai.dataSharingOn") }}</span>
            <span v-else> · {{ t("ai.dataSharingOff") }}</span>
          </template>
        </div>
      </div>
      <q-btn flat dense icon="add" :label="t('ai.newChat')" :disable="streaming" @click="newChat" />
      <q-btn flat dense icon="refresh" :disable="streaming || !lastFailed" :label="t('ai.retry')" @click="retry" />
    </div>

    <q-banner v-if="aiStatus && !aiStatus.configured" class="bg-warning text-dark q-mb-sm" dense>
      {{ t("ai.notConfigured") }}
    </q-banner>
    <q-banner v-if="bannerError" class="bg-negative text-white q-mb-sm" dense>
      {{ bannerError }}
      <template #action>
        <q-btn flat dense :label="t('ai.dismiss')" @click="bannerError = ''" />
      </template>
    </q-banner>

    <div v-if="attachments.length" class="q-mb-sm">
      <div class="text-caption q-mb-xs">{{ t("ai.attachments") }}</div>
      <div class="row q-gutter-xs">
        <q-chip
          v-for="att in attachments"
          :key="att.key"
          dense
          removable
          color="primary"
          text-color="white"
          :label="att.label"
          @remove="emit('remove-attachment', att)"
        />
      </div>
    </div>

    <div ref="scrollEl" class="col qka-ai-messages q-pa-sm">
      <div v-if="!messages.length" class="text-body2 text-grey-7">{{ t("ai.empty") }}</div>
      <div
        v-for="msg in messages"
        :key="msg.id"
        class="q-mb-md"
        :class="msg.role === 'user' ? 'qka-ai-user' : 'qka-ai-assistant'"
      >
        <div class="text-caption text-weight-medium q-mb-xs">
          {{ msg.role === "user" ? t("ai.you") : t("ai.assistant") }}
          <q-badge v-if="msg.status === 'streaming'" color="primary" :label="t('ai.streaming')" class="q-ml-sm" />
          <q-badge v-else-if="msg.status === 'error'" color="negative" :label="t('ai.error')" class="q-ml-sm" />
        </div>
        <div class="qka-ai-bubble">{{ msg.content || (msg.status === "streaming" ? "…" : "") }}</div>
        <div v-if="msg.error" class="text-negative text-caption q-mt-xs">{{ msg.error }}</div>
        <div v-if="msg.attachments?.length" class="q-mt-xs">
          <q-chip
            v-for="(a, idx) in msg.attachments"
            :key="idx"
            dense
            outline
            size="sm"
            :label="a.displayName || a.sourceRef || a.kind"
          />
        </div>
      </div>
    </div>

    <div class="row q-gutter-sm items-end q-mt-sm">
      <q-input
        v-model="draft"
        class="col"
        outlined
        dense
        autogrow
        :disable="!canSend && !streaming"
        :placeholder="t('ai.placeholder')"
        @keydown.enter.exact.prevent="send"
      />
      <q-btn
        v-if="streaming"
        color="negative"
        outline
        :label="t('ai.stop')"
        @click="stop"
      />
      <q-btn
        v-else
        color="primary"
        :loading="false"
        :disable="!canSend"
        :label="t('ai.send')"
        @click="send"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  api,
  type AiAttachmentRef,
  type AiConversationDetail,
  type AiMessage,
  type AiStatus,
} from "@/api";

const props = defineProps<{
  title?: string;
  documentIds?: string[];
  sampleIds?: string[];
  attachmentLabels?: Array<{ key: string; label: string; kind: "document" | "sample"; id: string }>;
}>();

const emit = defineEmits<{
  (e: "remove-attachment", att: { key: string; label: string; kind: "document" | "sample"; id: string }): void;
}>();

const { t } = useI18n();
const title = computed(() => props.title || t("ai.chat"));
const aiStatus = ref<AiStatus | null>(null);
const statusLoaded = ref(false);
const conversationId = ref<string | null>(null);
const messages = ref<AiMessage[]>([]);
const draft = ref("");
const streaming = ref(false);
const bannerError = ref("");
const lastFailed = ref(false);
const lastPayload = ref<{ message: string; documentIds: string[]; sampleIds: string[] } | null>(null);
const scrollEl = ref<HTMLElement | null>(null);
let abort: AbortController | null = null;

const attachments = computed(() => props.attachmentLabels ?? []);
const canSend = computed(
  () => Boolean(aiStatus.value?.configured) && Boolean(draft.value.trim()) && !streaming.value,
);

async function loadStatus() {
  try {
    aiStatus.value = await api.aiStatus();
  } catch (err) {
    bannerError.value = err instanceof Error ? err.message : t("ai.statusError");
  } finally {
    statusLoaded.value = true;
  }
}

async function scrollBottom() {
  await nextTick();
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
}

function newChat() {
  if (streaming.value) stop();
  conversationId.value = null;
  messages.value = [];
  draft.value = "";
  bannerError.value = "";
  lastFailed.value = false;
  lastPayload.value = null;
}

async function openConversation(id: string) {
  const detail: AiConversationDetail = await api.aiConversation(id);
  conversationId.value = detail.id;
  messages.value = detail.messages;
  await scrollBottom();
}

async function send() {
  const text = draft.value.trim();
  if (!text || streaming.value || !aiStatus.value?.configured) return;
  const documentIds = [...(props.documentIds ?? [])];
  const sampleIds = [...(props.sampleIds ?? [])];
  lastPayload.value = { message: text, documentIds, sampleIds };
  draft.value = "";
  lastFailed.value = false;
  bannerError.value = "";

  const tempUserId = `local-user-${Date.now()}`;
  const tempAssistantId = `local-asst-${Date.now()}`;
  messages.value.push({
    id: tempUserId,
    role: "user",
    content: text,
    status: "complete",
    error: null,
    createdAt: null,
    attachments: [
      ...documentIds.map((id) => ({ kind: "document", documentId: id, sampleId: null, displayName: id, sourceRef: null })),
      ...sampleIds.map((id) => ({ kind: "sample", documentId: null, sampleId: id, displayName: id, sourceRef: null })),
    ] as AiAttachmentRef[],
  });
  messages.value.push({
    id: tempAssistantId,
    role: "assistant",
    content: "",
    status: "streaming",
    error: null,
    createdAt: null,
    attachments: [],
  });
  await scrollBottom();

  streaming.value = true;
  abort = new AbortController();
  try {
    await api.aiChatStream(
      {
        message: text,
        conversationId: conversationId.value,
        documentIds,
        sampleIds,
      },
      {
        signal: abort.signal,
        onMeta(meta) {
          conversationId.value = meta.conversationId;
          const userIdx = messages.value.findIndex((m) => m.id === tempUserId);
          const asstIdx = messages.value.findIndex((m) => m.id === tempAssistantId);
          if (userIdx >= 0) messages.value[userIdx] = { ...messages.value[userIdx], id: meta.userMessageId };
          if (asstIdx >= 0) {
            messages.value[asstIdx] = {
              ...messages.value[asstIdx],
              id: meta.assistantMessageId,
              attachments: (meta.sources || []).map((s) => ({
                kind: s.kind,
                documentId: s.documentId,
                sampleId: s.sampleId,
                displayName: s.displayName,
                sourceRef: s.sourceRef,
              })),
            };
          }
        },
        onDelta(chunk) {
          const asst = messages.value.find((m) => m.id === tempAssistantId || m.status === "streaming");
          if (asst) asst.content += chunk;
          void scrollBottom();
        },
        onDone() {
          const asst = messages.value.find((m) => m.status === "streaming");
          if (asst) asst.status = "complete";
          void loadStatus();
        },
        onError(err) {
          lastFailed.value = true;
          bannerError.value = err.message;
          const asst = messages.value.find((m) => m.status === "streaming");
          if (asst) {
            asst.status = "error";
            asst.error = err.message;
            if (err.partial) asst.content = err.partial;
          }
        },
      },
    );
  } catch (err) {
    if ((err as Error)?.name === "AbortError") {
      const asst = messages.value.find((m) => m.status === "streaming");
      if (asst) {
        asst.status = "error";
        asst.error = t("ai.stopped");
      }
    } else {
      lastFailed.value = true;
      bannerError.value = err instanceof Error ? err.message : t("ai.sendError");
      const asst = messages.value.find((m) => m.status === "streaming");
      if (asst) {
        asst.status = "error";
        asst.error = bannerError.value;
      }
    }
  } finally {
    streaming.value = false;
    abort = null;
    await scrollBottom();
  }
}

function stop() {
  abort?.abort();
}

function retry() {
  if (!lastPayload.value || streaming.value) return;
  draft.value = lastPayload.value.message;
  void send();
}

onMounted(() => {
  void loadStatus();
});

watch(
  () => [props.documentIds, props.sampleIds],
  () => {
    /* attachments driven by parent */
  },
);

defineExpose({ newChat, openConversation, loadStatus, conversationId });
</script>

<style scoped>
.qka-ai-chat {
  min-height: 360px;
}
.qka-ai-messages {
  overflow: auto;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.02);
  min-height: 220px;
}
.body--dark .qka-ai-messages {
  border-color: rgba(255, 255, 255, 0.18);
  background: rgba(255, 255, 255, 0.04);
}
.qka-ai-bubble {
  white-space: pre-wrap;
  font-size: 0.92rem;
  line-height: 1.45;
}
.qka-ai-user .qka-ai-bubble {
  opacity: 0.95;
}
</style>
