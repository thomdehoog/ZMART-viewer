// A wheel may only package the output of a completed build of these inputs.
//
// The inputs are everything the page is built from: the interface, the
// engine's drawing code, these build scripts, and the three files at the
// root that describe the build. The outputs are what lands in gui/built.
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
// Python files in gui/ open the window; they are not part of the page.
const page = path => !path.endsWith(".py");
const inputs = [
  ...(await files(join(root, "gui"), ["built", "node_modules", "__pycache__"])).filter(page),

  ...await files(join(root, "engine", "drawing"), ["__pycache__"]),
  ...await files(join(root, "scripts")),
  join(root, "package.json"), join(root, "package-lock.json"), join(root, "vite.config.js"),
];
const built = join(root, "gui", "built");
const outputs = (await files(built)).filter(path => !path.endsWith("build-manifest.json"));
await writeFile(join(built, "build-manifest.json"), JSON.stringify({
  inputs: await hashes(inputs), outputs: await hashes(outputs),
}));
