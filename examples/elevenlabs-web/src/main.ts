// A web call to an ElevenLabs agent whose prompt comes from sopops.
import { Conversation } from "@elevenlabs/client";
import { SopOps } from "sopops";
import { elevenLabsSession } from "sopops/elevenlabs";

const sopops = new SopOps({ url: import.meta.env.VITE_SOPOPS_URL, token: import.meta.env.VITE_SOPOPS_TOKEN });
const agent = import.meta.env.VITE_ELEVENLABS_AGENT;
const stream = import.meta.env.VITE_SOPOPS_STREAM === "1";

const button = document.querySelector<HTMLButtonElement>("#call")!;
const status = document.querySelector<HTMLSpanElement>("#status")!;
const log = document.querySelector<HTMLOListElement>("#log")!;

let conversation: Conversation | null = null;

function show(role: string, text: string) {
  const li = document.createElement("li");
  li.dataset.role = role;
  li.textContent = text;
  log.append(li);
}

button.addEventListener("click", async () => {
  if (conversation) {
    await conversation.endSession();
    return;
  }
  button.disabled = true;
  try {
    await navigator.mediaDevices.getUserMedia({ audio: true });

    // The live release's prompt for this agent, as startSession options (or the last good copy if sopops is down).
    const s = await elevenLabsSession(sopops, { agent, stream });
    status.textContent = `${s.instructions.agent}, release ${s.instructions.release ?? "none"} (${s.instructions.source})`;

    // Spread ours in; where we add our own handler, call ours from it.
    const ours = s.sessionOptions;
    conversation = await Conversation.startSession({
      ...ours,
      onMessage: (m) => {
        ours.onMessage?.(m);
        show(m.role, m.message);
      },
      onAgentToolResponse: (r) => {
        ours.onAgentToolResponse?.(r);
        show("tool", `${r.tool_name}${r.is_error ? " (error)" : ""}`);
      },
      onDisconnect: (d) => {
        ours.onDisconnect?.(d);
        conversation = null;
        button.textContent = "Call";
      },
    });
    button.textContent = "Hang up";
  } catch (e) {
    status.textContent = e instanceof Error ? e.message : String(e);
  } finally {
    button.disabled = false;
  }
});
