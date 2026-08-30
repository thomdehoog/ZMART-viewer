# The machine these figures were measured on

Every number in `ladder.jsonl` and every photograph in `shots/` was taken on
this hardware, on 30 August 2026. None of it says what a lab workstation
would do; it says what *this* box did, which is what makes the rungs
comparable with each other.

| | |
|---|---|
| CPU | Intel Xeon @ 2.80 GHz — 4 cores, 4 threads (1 socket, no SMT), AVX-512 |
| Memory | 15 GiB |
| Disk | virtual disk (`/dev/vda`), 252 GB volume, cloud-container allowance |
| Kernel | Linux 6.18.44 (containerised cloud environment) |
| Python | 3.11.15, zarr 3.1.6, numpy 2.4.6 |
| Browser drawing | **software** — Chromium headless on SwiftShader (ANGLE/Vulkan), no GPU |

Two consequences worth remembering when reading the figures:

- **Frame timings flatter nothing here.** The ~17.5 ms frame gap in every
  `look` is SwiftShader rasterising on the CPU; a machine with any real
  graphics card draws this picture many times faster, so the browser-side
  numbers are an upper bound and comparable only between rungs, not with a
  workstation.
- **Serving and composing numbers share 4 cores with everything else** —
  the browser, the server, and the benchmark itself run on the same four
  CPUs. Real deployments separate at least the browser from the server.
