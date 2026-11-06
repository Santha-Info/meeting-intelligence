<script setup>
import { nextTick, ref, watch } from "vue";
import { api, fmtTime } from "../api.js";

const props = defineProps({
  meeting: { type: Object, default: null },
  jumpTo: { type: Object, default: null },
});

const turns = ref([]);
const filter = ref("");
const container = ref(null);

async function load() {
  turns.value = props.meeting ? (await api.transcript(props.meeting.id)).turns : [];
  await scrollToJump();
}

function inRange(turn) {
  const j = props.jumpTo;
  return j && props.meeting && j.meetingId === props.meeting.id && turn.start >= j.start && turn.start < Math.max(j.end, j.start + 1);
}

function visible(turn) {
  const f = filter.value.trim().toLowerCase();
  return !f || turn.text.toLowerCase().includes(f) || turn.speaker.toLowerCase().includes(f);
}

async function scrollToJump() {
  await nextTick();
  container.value?.querySelector(".turn.highlight")?.scrollIntoView({ behavior: "smooth", block: "center" });
}

watch(() => props.meeting?.id, load, { immediate: true });
watch(() => props.jumpTo?.at, scrollToJump);
</script>

<template>
  <section class="panel transcript">
    <p v-if="!meeting" class="empty">Select a meeting in the sidebar.</p>
    <template v-else>
      <div class="insights-head">
        <div>
          <h2>{{ meeting.title }}</h2>
          <p class="muted">{{ meeting.turn_count }} turns · {{ fmtTime(meeting.duration_seconds) }}</p>
        </div>
        <input v-model="filter" class="search" placeholder="Filter by word or speaker…" />
      </div>
      <ol ref="container" class="turns">
        <li v-for="t in turns" v-show="visible(t)" :key="t.index" class="turn" :class="{ highlight: inRange(t) }">
          <span class="time">{{ fmtTime(t.start) }}</span>
          <span class="speaker">{{ t.speaker }}</span>
          <p>{{ t.text }}</p>
        </li>
      </ol>
    </template>
  </section>
</template>
