// Thin API client. All calls go to /api (proxied by Vite in dev, same origin in the container).

async function json(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

export const api = {
  health: () => fetch("/api/health").then(json),
  meetings: () => fetch("/api/meetings").then(json),
  meeting: (id) => fetch(`/api/meetings/${encodeURIComponent(id)}`).then(json),
  transcript: (id) => fetch(`/api/meetings/${encodeURIComponent(id)}/transcript`).then(json),
  actionItems: () => fetch("/api/action-items").then(json),
  loadSamples: () => fetch("/api/meetings/samples", { method: "POST" }).then(json),
  remove: (id) => fetch(`/api/meetings/${encodeURIComponent(id)}`, { method: "DELETE" }).then(json),
  regenerateInsights: (id) =>
    fetch(`/api/meetings/${encodeURIComponent(id)}/insights`, { method: "POST" }).then(json),
  upload(files) {
    const form = new FormData();
    for (const f of files) form.append("files", f);
    return fetch("/api/meetings", { method: "POST", body: form }).then(json);
  },

  // Server-Sent Events over a POST body. EventSource only supports GET, so we parse the stream ourselves.
  async chat(payload, { onEvent, signal }) {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
    if (!res.ok || !res.body) await json(res);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const event = /^event: (.*)$/m.exec(frame)?.[1] ?? "message";
        const data = /^data: (.*)$/m.exec(frame)?.[1];
        if (data) onEvent(event, JSON.parse(data));
      }
    }
  },
};

export function fmtTime(seconds) {
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${String(m).padStart(2, "0")}:${sec}`;
}

export function parseTs(ts) {
  // "mm:ss" or "hh:mm:ss" -> seconds
  const parts = String(ts).split(":").map(Number);
  if (parts.some(Number.isNaN)) return null;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}
