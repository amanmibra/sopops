// Our startSession options type-check against @elevenlabs/client's own types (a dev dependency only).
import type { PartialOptions } from "@elevenlabs/client";
import { expect, it } from "vitest";
import type { ElevenLabsSession } from "../src/elevenlabs.js";

it("matches @elevenlabs/client's startSession options", () => {
  const check = (s: ElevenLabsSession): PartialOptions => ({ ...s.sessionOptions, onMessage: () => {} });
  expect(typeof check).toBe("function");
});
