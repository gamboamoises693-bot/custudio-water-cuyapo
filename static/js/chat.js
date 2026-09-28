/**
 * Chat live-update behavior.
 *
 * The spec asks for "LIVE updates via Firebase onSnapshot (same as OMG Ice real-time)".
 * Firestore's realtime onSnapshot listener is a client-side (browser) SDK feature that
 * requires exposing a Firebase Web App config (apiKey, projectId, etc.) to the browser
 * and using Firestore security rules to control access. Since this server currently
 * talks to Firestore only through the trusted Admin SDK (no public web config / rules
 * are set up yet), this file uses short-interval polling against a small JSON endpoint
 * as a safe drop-in that behaves the same way from the UI's perspective (new messages
 * appear automatically, no page reload).
 *
 * To upgrade to true onSnapshot later:
 *   1. Add a Firebase Web App in the Firebase Console, get its config object.
 *   2. Include the firebase-app / firebase-firestore CDN scripts here.
 *   3. Write Firestore security rules scoped per customer/thread.
 *   4. Replace pollMessages() below with a firestore.collection('chat_messages')
 *      .where('thread_id','==', threadId).onSnapshot(...) listener.
 */

(function () {
  const messagesBox = document.getElementById("messagesBox");
  if (!messagesBox) return;

  const threadId = messagesBox.dataset.threadId;
  const POLL_INTERVAL_MS = 3000;

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  function renderMessage(m) {
    const isCustomer = m.sender_type === "customer";
    const bubbleClasses = isCustomer
      ? "bg-gray-200 text-gray-800"
      : "bg-aqua-500 text-white";
    const align = isCustomer ? "justify-start" : "justify-end";

    let inner = "";
    if (m.message_type === "image" && m.image_url) {
      inner += `<img src="${m.image_url}" class="rounded-lg max-w-full mb-1" alt="chat image">`;
    }
    if (m.message_text) {
      inner += `<p>${escapeHtml(m.message_text)}</p>`;
    }
    inner += `<p class="text-[10px] opacity-70 mt-1 capitalize">${m.sender_type}</p>`;

    return `<div class="flex ${align}"><div class="max-w-[75%] rounded-2xl px-4 py-2 text-sm ${bubbleClasses}">${inner}</div></div>`;
  }

  let lastMessageIds = new Set(
    Array.from(messagesBox.querySelectorAll("[data-msg-id]")).map((el) => el.dataset.msgId)
  );

  async function pollMessages() {
    try {
      const res = await fetch(`/chats/${threadId}/poll`, { credentials: "same-origin" });
      if (!res.ok) return;
      const data = await res.json();
      const currentIds = new Set(data.messages.map((m) => m.id));

      // Only re-render if something actually changed (new message arrived).
      const changed =
        currentIds.size !== lastMessageIds.size ||
        [...currentIds].some((id) => !lastMessageIds.has(id));

      if (changed) {
        messagesBox.innerHTML = data.messages.map(renderMessage).join("") ||
          '<p class="text-center text-sm text-gray-400 mt-10">Simulan ang usapan...</p>';
        messagesBox.scrollTop = messagesBox.scrollHeight;
        lastMessageIds = currentIds;
      }
    } catch (err) {
      // Silently ignore transient network errors - next poll will retry.
      console.warn("Chat poll failed:", err);
    }
  }

  messagesBox.scrollTop = messagesBox.scrollHeight;
  setInterval(pollMessages, POLL_INTERVAL_MS);

  // Quick reply buttons fill the text input instead of sending immediately,
  // so staff can still tweak the message before hitting send.
  document.querySelectorAll(".quick-reply").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById("messageInput");
      if (input) {
        input.value = btn.dataset.text;
        input.focus();
      }
    });
  });
})();
