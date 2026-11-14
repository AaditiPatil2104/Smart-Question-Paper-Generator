const aiBtn = document.getElementById("ai-btn");
const aiWindow = document.getElementById("ai-window");
const closeBtn = document.getElementById("close-ai");
const newBtn = document.getElementById("new-ai");
const quickActions = document.querySelectorAll(".quick-actions button");
const sendBtn = document.getElementById("send-btn");
const messageInput = document.getElementById("message");
const aiBody = document.getElementById("ai-body");
const uploadBtn = document.getElementById("upload-btn");
const syllabusFile = document.getElementById("syllabus-file");
const uploadName = document.getElementById("upload-name");

function openAI() {
    aiWindow.style.display = "flex";
    aiWindow.setAttribute("aria-hidden", "false");
    setTimeout(() => messageInput.focus(), 100);
}
function closeAI() {
    aiWindow.style.display = "none";
    aiWindow.setAttribute("aria-hidden", "true");
}
async function startNewConversation() {
    try { await fetch("/api/ai-assistant/reset", { method: "POST" }); } catch (e) {}
    aiBody.innerHTML = `<div class="bot-message welcome-message"><div class="message-title">👋 Hello!</div><p>Let's start a fresh question set. What subject are we working on?</p><p class="ai-hint">I’ll guide you through syllabus → type → number → difficulty → generation → automatic saving.</p></div>`;
    messageInput.focus();
}
function escapeHtml(text) {
    return String(text).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function addMessage(text, type = "bot") {
    const div = document.createElement("div");
    div.className = type === "user" ? "user-message" : "bot-message";
    div.innerHTML = text;
    aiBody.appendChild(div);
    aiBody.scrollTop = aiBody.scrollHeight;
    return div;
}
async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text) return;

    addMessage(escapeHtml(text), "user");
    messageInput.value = "";

    // Show an immediate thinking message while the server prepares the questions.
    const isGenerate = /^(generate|generate questions|go|yes)$/i.test(text);
    let thinkingBubble = null;
    if (isGenerate) {
        thinkingBubble = addMessage("⏳ Please wait… I’m working on your questions and checking them against the uploaded syllabus.", "bot");
        thinkingBubble.classList.add("thinking-message");
    }

    try {
        const response = await fetch("/api/ai-assistant/message", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text })
        });
        const data = await response.json();
        if (thinkingBubble) thinkingBubble.remove();

        if (data.thinking) addMessage(data.thinking, "bot");
        if (data.message) addMessage(data.message, "bot");
        if (data.review) {
            // Show the saved-question preview after the confirmation message.
            setTimeout(() => addMessage(data.review, "bot"), 250);
        } else if (!data.message) {
            addMessage("Something went wrong.", "bot");
        }
    } catch (error) {
        if (thinkingBubble) thinkingBubble.remove();
        addMessage("❌ Could not connect to the assistant. Please check that Flask is running.", "bot");
    }
}
async function uploadSyllabus(file) {
    if (!file) return;
    uploadName.textContent = file.name;
    addMessage(`📎 Uploaded: <b>${escapeHtml(file.name)}</b>`, "user");
    const formData = new FormData();
    formData.append("syllabus", file);
    try {
        const response = await fetch("/api/ai-assistant/upload-syllabus", { method: "POST", body: formData });
        const data = await response.json();
        addMessage(data.message || "Could not upload the syllabus.", "bot");
    } catch (error) {
        addMessage("❌ Syllabus upload failed. Please try again.", "bot");
    }
}

if(aiBtn) aiBtn.addEventListener("click", openAI);
if(newBtn) newBtn.addEventListener("click", startNewConversation);
quickActions.forEach(btn => btn.addEventListener("click", () => { messageInput.value = btn.dataset.message || ""; sendMessage(); }));
if(closeBtn) closeBtn.addEventListener("click", closeAI);
if(sendBtn) sendBtn.addEventListener("click", sendMessage);
if(messageInput) messageInput.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); sendMessage(); } });
if(uploadBtn) uploadBtn.addEventListener("click", () => syllabusFile.click());
if(syllabusFile) syllabusFile.addEventListener("change", () => uploadSyllabus(syllabusFile.files[0]));
