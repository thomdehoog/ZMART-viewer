// A wheel may only package the output of a completed build of these inputs.
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join, relative } from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
async function files(folder, recursive = true) {
  const entries = await readdir(folder, { withFileTypes: true });
  return (await Promise.all(entries.map(entry => entry.isFile() ? [join(folder, entry.name)] :
    recursive ? files(join(folder, entry.name)) : []))).flat();
}
async function hashes(paths) {
  return Object.fromEntries(await Promise.all(paths.sort().map(async path => [
    relative(root, path).replaceAll("\\", "/"),
    createHash("sha256").update(await readFile(path)).digest("hex"),
  ])));
}
const inputs = [...await files(root, false), ...await files(join(root, "src")),
  ...await files(join(root, "scripts")),
  ...(existsSync(join(root, "public")) ? await files(join(root, "public")) : [])];
const outputs = (await files(join(root, "dist"))).filter(path => !path.endsWith("build-manifest.json"));
await writeFile(join(root, "dist", "build-manifest.json"), JSON.stringify({
  inputs: await hashes(inputs), outputs: await hashes(outputs),
}));
