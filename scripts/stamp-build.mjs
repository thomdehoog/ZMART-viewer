// A wheel may only package the output of a completed build of these inputs.
//
// The inputs are everything the page is built from: the interface, the
// engine's drawing code, these build scripts, and the three files at the
// root that describe the build. The outputs are what lands in gui/dist.
import { createHash } from "node:crypto";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join, relative } from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
async function files(folder, skip = []) {
  const entries = await readdir(folder, { withFileTypes: true });
  return (await Promise.all(entries.map(entry => entry.isFile() ? [join(folder, entry.name)] :
    skip.includes(entry.name) ? [] : files(join(folder, entry.name), skip)))).flat();
}
async function hashes(paths) {
  return Object.fromEntries(await Promise.all(paths.sort().map(async path => [
    relative(root, path).replaceAll("\\", "/"),
    createHash("sha256").update(await readFile(path)).digest("hex"),
  ])));
}
const inputs = [
  ...await files(join(root, "gui"), ["dist", "node_modules", "__pycache__"]),

  ...await files(join(root, "engine", "drawing"), ["__pycache__"]),
  ...await files(join(root, "scripts")),
  join(root, "package.json"), join(root, "package-lock.json"), join(root, "vite.config.js"),
];
const dist = join(root, "gui", "dist");
const outputs = (await files(dist)).filter(path => !path.endsWith("build-manifest.json"));
await writeFile(join(dist, "build-manifest.json"), JSON.stringify({
  inputs: await hashes(inputs), outputs: await hashes(outputs),
}));
