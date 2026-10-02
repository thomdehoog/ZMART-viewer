// A wheel may only package the output of a completed build of these inputs.
//
// The inputs are everything the page is built from: the whole of gui/source
// (the interface, its drawing code, these build scripts and the files that
// describe the build) and the drawing code the engine keeps for itself in
// engine/drawing. The outputs are what lands in gui/build. Every path is
// recorded relative to the repository, which is where build_support.py checks
// them from.
import { createHash } from "node:crypto";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join, relative } from "node:path";

const root = fileURLToPath(new URL("../../../", import.meta.url));
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
  ...await files(join(root, "gui", "source"), ["node_modules"]),
  ...await files(join(root, "engine", "drawing"), ["__pycache__"]),
];
const built = join(root, "gui", "build");
const outputs = (await files(built)).filter(path => !path.endsWith("build-manifest.json"));
await writeFile(join(built, "build-manifest.json"), JSON.stringify({
  inputs: await hashes(inputs), outputs: await hashes(outputs),
}));
