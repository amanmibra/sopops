/** Node-only helpers: a file cache, so the last good instructions survive restarts. */
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { createHash, randomBytes } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";
import type { InstructionCache, Instructions } from "./index.js";

/** Keeps each agent's last good instructions as a JSON file in `dir` (default `SOPOPS_CACHE_DIR` or `~/.cache/sopops`). */
export class FileCache implements InstructionCache {
  readonly dir: string;
  constructor(dir?: string) {
    this.dir = dir ?? process.env.SOPOPS_CACHE_DIR ?? join(homedir(), ".cache", "sopops");
  }
  private file(key: string): string {
    return join(this.dir, createHash("sha256").update(key).digest("hex").slice(0, 24) + ".json");
  }
  async get(key: string): Promise<Instructions | undefined> {
    try {
      return JSON.parse(await readFile(this.file(key), "utf8")) as Instructions;
    } catch {
      return undefined;
    }
  }
  async set(key: string, value: Instructions): Promise<void> {
    await mkdir(this.dir, { recursive: true });
    const tmp = join(this.dir, `.tmp-${randomBytes(6).toString("hex")}`);
    await writeFile(tmp, JSON.stringify(value));
    await rename(tmp, this.file(key)); // atomic: a crash never leaves a broken copy
  }
}
