<script setup>
import { computed, onMounted, ref } from "vue";
import { api } from "./api.js";
import MeetingSidebar from "./components/MeetingSidebar.vue";
import ChatPanel from "./components/ChatPanel.vue";
import InsightsPanel from "./components/InsightsPanel.vue";
import TranscriptPanel from "./components/TranscriptPanel.vue";

const health = ref(null);
const meetings = ref([]);
const selectedIds = ref([]); // scope for chat; empty = all meetings
const activeMeetingId = ref(null); // meeting shown in Insights / Transcript
const tab = ref("ask");
const jumpTo = ref(null); // { meetingId, start, end } from a clicked citation
const error = ref("");

const activeMeeting = computed(() => meetings.value.find((m) => m.id === activeMeetingId.value) ?? null);

async function refresh() {
  try {
    meetings.value = await api.meetings();
    if (!activeMeetingId.value && meetings.value.length) activeMeetingId.value = meetings.value[0].id;
    selectedIds.value = selectedIds.value.filter((id) => meetings.value.some((m) => m.id === id));
    if (activeMeetingId.value && !meetings.value.some((m) => m.id === activeMeetingId.value)) {
      activeMeetingId.value = meetings.value[0]?.id ?? null;
    }
  } catch (e) {
    error.value = e.message;
  }
}

function openCitation(citation) {
  activeMeetingId.value = citation.meeting_id;
  jumpTo.value = { meetingId: citation.meeting_id, start: citation.start, end: citation.end, at: Date.now() };
  tab.value = "transcript";
}

function openMeeting(id, target = "insights") {
  activeMeetingId.value = id;
  tab.value = target;
}

onMounted(async () => {
  try {
    health.value = await api.health();
  } catch (e) {
    error.value = `Backend not reachable: ${e.message}`;
  }
  await refresh();
});
</script>

<template>
  <div class="layout">
    <MeetingSidebar
      :meetings="meetings"
      :selected-ids="selectedIds"
      :active-id="activeMeetingId"
      :health="health"
      @update:selected-ids="selectedIds = $event"
      @open="openMeeting"
      @changed="refresh"
      @error="error = $event"
    />

    <main class="main">
      <header class="topbar">
        <nav class="tabs" role="tablist">
          <button :class="{ active: tab === 'ask' }" @click="tab = 'ask'">Ask</button>
          <button :class="{ active: tab === 'insights' }" @click="tab = 'insights'">Insights</button>
          <button :class="{ active: tab === 'transcript' }" @click="tab = 'transcript'">Transcript</button>
        </nav>
        <div v-if="health" class="mode" :class="health.llm_mode">
          <span class="dot" />
          {{ health.llm_mode === "anthropic" ? health.model : "Offline demo mode (no API key)" }}
        </div>
      </header>

      <p v-if="error" class="banner error" @click="error = ''">{{ error }} <small>(click to dismiss)</small></p>

      <ChatPanel
        v-show="tab === 'ask'"
        :meetings="meetings"
        :selected-ids="selectedIds"
        @cite="openCitation"
      />
      <InsightsPanel
        v-if="tab === 'insights'"
        :meeting="activeMeeting"
        :meetings="meetings"
        @jump="openCitation"
        @changed="refresh"
        @error="error = $event"
      />
      <TranscriptPanel v-if="tab === 'transcript'" :meeting="activeMeeting" :jump-to="jumpTo" />
    </main>
  </div>
</template>
