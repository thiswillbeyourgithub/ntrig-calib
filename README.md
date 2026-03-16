# ntrig-calib — Surface Pro 3 Touchscreen Dead Zone Fix (Linux)

> **TL;DR:** The Surface Pro 3's N-Trig digitizer develops a dead rectangular strip over time due to firmware calibration drift. On Windows, Sony/N-Trig's `CalibG4.exe` fixes it in seconds. This repo contains a Python script that replicates what `CalibG4.exe` does — over Linux's `/dev/hidraw` interface — so you never need to boot Windows.

**This tool was developed entirely by Claude Opus 4.6 with extended thinking (Anthropic's AI assistant), via the Claude mobile app on an iPhone.** Given only a description of the symptom, Claude diagnosed the root cause, found the Sony VAIO update package online, deobfuscated its XOR-inverted cabinet archive, reverse engineered the proprietary NCP protocol from the extracted DLL using [Ghidra](https://ghidra-sre.org/), and wrote the working Python script across multiple sessions. The story is below.

---

## Table of Contents

- [The Problem](#the-problem)
- [The Fix](#the-fix)
- [The Full Story](#the-full-story)
- [Usage](#usage)
- [Credits](#credits)
- [Related Issues](#related-issues)
- [License](#license)

---

## The Problem

Surface Pro 3 units develop a **dead horizontal or vertical strip** on their touchscreen where touch input is completely unresponsive. Pen input still works in the dead zone. The strip survives reboots, driver reinstalls, and kernel upgrades.

**This is not a driver bug.** It is firmware-level calibration drift in the N-Trig DuoSense digitizer. The calibration data lives in the digitizer's own non-volatile memory and degrades over time, independently of the OS.

A known trigger is the **Type Cover's magnetic attachment strip**: repeated contact of the keyboard cover's magnets with the screen edge can corrupt calibration data in the affected region. Multiple SP3 users report dead zones appearing specifically along the bottom edge after heavy Type Cover use.


---

## The Fix

Running `CalibG4.exe` on Windows recalibrates the digitizer's firmware. Because the calibration is stored in the digitizer's own EEPROM — not in Windows — running it once from Windows fixes the dead zone on Linux too.

**This script does the same thing from Linux, without Windows.**

---

## The Full Story

### Background

The entire process — from diagnosis to working script — was driven by Claude Opus 4.6 with extended thinking, running in the Claude mobile app on an iPhone. The user described the symptom (dead touch strip, pen still working). Claude identified it as a known N-Trig firmware calibration issue solvable in software, located `CalibG4.exe` inside a Sony VAIO update package online, deobfuscated the XOR-inverted cabinet, reverse engineered the proprietary NCP protocol from the DLL using [Ghidra](https://ghidra-sre.org/), and wrote the Python script across multiple sessions. The user's role was describing the problem and testing the result.

The Windows fix (`CalibG4.exe`) communicates with the N-Trig digitizer via a proprietary binary protocol called **NCP (N-Trig Communication Protocol)**. The tool is distributed only as part of a Sony VAIO driver update package, and no documentation or Linux equivalent has ever existed publicly.

### Finding the tool

Claude located `CalibG4.exe` inside `EP0000601624.exe`, a Sony VAIO Update self-extracting wrapper available from Sony's support pages, referenced in Surface forums for years as the fix for SP3 dead zones ([example thread](https://answers.microsoft.com/en-us/surface/forum/all/does-anyone-still-have-the-calibg4exe-touch-screen/eb5376c3-1e59-474a-80df-00f918c8f9a6)).

Extracting it isn't obvious: the wrapper embeds a cabinet file that is **XOR-inverted** (each byte XORed with 0xFF) inside a PE resource blob. Once you invert the bytes and `cabextract`, you get:
- `CalibG4.exe` (19 KB) — the calibration tool
- `NCPTransportInterface.dll` (151 KB) — the HID communication layer

### Reverse engineering the DLL

Claude then used [Ghidra](https://ghidra-sre.org/) to disassemble the DLL and understand exactly what bytes to send over the Linux hidraw interface.

**Stage 1 — Initial analysis** revealed:
- `CalibG4.exe` sends two NCP commands: `START_CALIB` (group=0x20, id=0x0A) and polls `GET_STATUS` (group=0x20, id=0x0B)
- Status responses: `\x42\x42\x42` ("BBB") = complete, `\x63\x63\x63` ("ccc") = in progress, `\x21\x21\x21` ("!!!") = unknown/intermediate ("Unknown status, waiting")
- All actual communication goes through `NCPTransportInterface.dll`

**Stage 2 — NCP frame format** (from Ghidra disassembly of `0x18000D0D0`):

```
Byte  0:     0x7E  — start marker
Bytes 1-2:   Module ID (LE uint16) — DLL derives this via UuidCreate() at runtime; 0x0001 is a working hardcoded substitute
Bytes 3-4:   Total frame size (LE uint16) = 14 + payload_len + 1
Byte  5:     Flags: 0x01 = expects response, 0x41 = fire-and-forget
Byte  6:     Command group (0x20 = calibration)
Byte  7:     Command ID (0x0A = start, 0x0B = get status)
Bytes 8-11:  Sequence number (LE uint32) — usually 0
Bytes 12-13: Reserved (0x00)
Bytes 14..:  Payload
Last byte:   Checksum = (-sum(signed_bytes)) & 0xFF
```

**Stage 3 — The hidraw dead end (v1–v3)**

The Linux-exposed HID report descriptor is **455 bytes** and defines **16 report IDs**: `0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0C, 0x11, 0x15, 0x18, 0x1B, 0x58`. The "direct path" reports `0x29–0x35` mentioned in Stage 4 are physically absent from this descriptor — they live in separate HID collections (distinct PDOs in the Windows device tree) that Linux never exposes under `/dev/hidrawN`.

Early attempts sent NCP frames via `HIDIOCG/SFEATURE` on report 0x1B (259 bytes, the largest vendor report in the descriptor). SET_FEATURE would succeed (kernel accepted it) but the device never responded. Reading 0x1B always returned the same static bytes regardless of what was written (`29 a9 19 9f 9a 19 a4 ...`), confirming it is a crypto/auth state register that silently ignores writes. This investigation was partly delayed by a bug in the diagnostic probe loop: it tested buffer sizes `[8, 16, 34, 65, 260, 514]` in order and stopped at 8 when reading 0x1B "succeeded" — meaning it never actually read the full 259-byte response, masking the fact that those bytes are always static. Report 0x03 changed on every write, which looked promising but turned out to be just a HID transaction counter incrementing.

Raw I2C access also failed — unbinding the `i2c_hid_acpi` driver powers the device down, and it wouldn't respond to I2C commands afterward.

**Stage 4 — Deep DLL analysis (v4–v5, the breakthrough at v5)**

Claude disassembled the I2C transport class's vtable in Ghidra and traced the full call graph. The key discovery was a **fork in the send function** (`0x1800088C0`) based on a capability flag `[this+0x7c]`:

```
if [this+0x7c] != 0:
    → CHUNKED PATH: report 0x05, 61-byte chunks
else:
    → DIRECT PATH: reports 0x29–0x2D (I2C direct) or 0x2E–0x35 (USB-HID)
```

The flag `[this+0x7c]` is not set unconditionally — it is only activated after a **capability probe sequence** (function `0x1800095d0`) in which the DLL compares capabilities against three probe configurations (cmd_ids `0x01`, `0x0B`, `0x0C`). On the SP3 running Windows one of these presumably succeeds and enables chunked mode; the Ghidra analysis strongly suggests `0x0C` is the triggering probe, though this was not directly confirmed on Linux. The "direct path" reports (0x29–0x2D for I2C, 0x2E–0x35 for USB-HID) map to separate HID collections — distinct PDOs in the Windows HID device tree — which is why they don't appear as separate nodes under Linux's single `/dev/hidrawN` interface.

The chunked protocol (function `0x18000CC80`):
```
Each HID write = 61 bytes:
  [0x05] [remaining_chunks] [59 bytes of NCP frame data]

remaining_chunks counts DOWN: last chunk = 0
```

For a 15-byte NCP frame (no payload), this is a single 61-byte write:
```
[0x05] [0x00] [7e 01 00 0f 00 01 20 0a 00 00 00 00 00 47] [zeros to pad to 61]
```

**Stage 5 — The async response**

Another key finding: the DLL's receive thread uses `ReadFile` on the HID device handle (async I/O), **not** `HidD_GetFeature`. On Linux this maps to a non-blocking `read()` on the hidraw fd. Previous script versions were polling GET_FEATURE, completely missing the responses. The v5 script uses `select()` + `read()` and immediately captures the NCP response on **report 0x06** (an empirical observation — the chat analysis identified 0x0B and 0x0C as candidate response reports, but responses actually arrived on 0x06; no deeper explanation was found):

```
Input report 0x06: 7e 01 00 12 00 81 20 0b 00 00 00 00 00 00 21 21 21 60 ...
                   ^^                    ^^
                   NCP marker            cmd_group=0x20, cmd_id=0x0B (GET_STATUS response)
                                                                      ^^^^^^^^^^^
                                                                      payload = "!!!" = unknown/intermediate state
```

The `0x81` in the flags byte (bit 7 set) indicates a **response frame**. The `!!!` payload maps to the string `"Unknown status, waiting"` in `CalibG4.exe` — it is an intermediate polling state, not a confirmed trigger. The DLL continues polling after receiving it. The screen was confirmed fully fixed in a subsequent session (the "it worked" moment is not captured in the analysis chat logs).

**Full CalibG4 call sequence (from Ghidra):**
- Read buffer: 4096 bytes
- `START_CALIB` timeout: 3000 ms
- Status poll loop: up to 60 iterations × 500 ms = 30 seconds max
- Explicit `DeInit` + `Deregister` cleanup at end

### Summary of failed approaches (for future reference)

| Approach | Why it failed |
|---|---|
| Sending NCP to report 0x1B | Wrong report. 0x1B is a crypto/auth state register — returns identical static bytes (`29 a9 19 9f 9a 19 a4 ...`) on every read and silently ignores writes. |
| Raw I2C after unbinding driver | `i2c_hid_acpi` powers device down on unbind; EREMOTEIO on all commands. |
| Polling GET_FEATURE for responses | The DLL uses async ReadFile, not GetFeature. Responses come as input reports. |
| iptsd / linux-surface kernel | Completely wrong technology. SP3 doesn't use IPTS. |
| Report 0x03 changes as signal | It's a transaction counter, not an NCP response. |

---

## Usage

```bash
# Requires root (hidraw access)
sudo python3 ntrig_calib.py

# Or explicitly specify the device
sudo python3 ntrig_calib.py -d /dev/hidraw1
```

The script will:
1. Find the N-Trig hidraw device automatically
2. Send a calibration start command via the NCP protocol
3. Poll for completion (up to ~5 seconds)
4. Report success

**After running, touch the previously-dead area.** It should respond immediately. No reboot required.

> **Note on longevity:** A [2017 report from René Rebe](https://web.archive.org/web/20250324201828/https://rene.rebe.de/2017-07-29/n-trig-touch-screens-occasionally-need-re-calibration/) ([video](https://www.youtube.com/watch?v=mVX-7ZI8ysk)) noted that calibration may be temporary on some units — lasting only "a day, or a boot or two" before drift recurs. If the dead zone comes back, re-run the script. Frequent recurrence may indicate the digitizer hardware is physically degrading.

### CalibG4.exe and NCPTransportInterface.dll

The Windows binaries (`CalibG4.exe` and `NCPTransportInterface.dll`) were extracted from [EP0000601624.zip](https://gartnertechnology.com/wp-content/uploads/2024/01/EP0000601624.zip) (also [saved by the Internet Archive](https://web.archive.org/web/20260315181048/https://gartnertechnology.com/wp-content/uploads/2024/01/EP0000601624.zip)). As long as these links are up, I don't want to republish these files. If you have trouble finding them, open an issue and I'll see if it's okay to send them to you or something.

**SHA-256 checksums:**
```
822a319fc8bb3d3a9fce50f9610124f3838c20a638f727c70c984fe88356ba44  EP0000601624.zip
89160d12677f2bd98f21db01651677d62dd0c242082bc9591edf41e330d7dd91  NCPTransportInterface.dll
ebf0168a60111d58f7709cfa8c7d129002cbdb192f253dddad6737122ddbdde7  CalibG4.exe
```

### Requirements

- Python 3.6+
- Root privileges
- The N-Trig device at `/dev/hidraw*` (verify with `lsusb -t` or `ls /dev/hidraw*`)
- Kernel with `hid-multitouch` bound to `NTRG0001:01 1B96:1B05` (standard on Ubuntu 20.04+)


---

## Credits

- **Diagnosis, reverse engineering, and script**: Done entirely by [Claude Opus 4.6](https://claude.ai) with extended thinking (Anthropic), via the Claude mobile app on an iPhone. Given only a symptom description, Claude identified the calibration drift root cause, located the Sony VAIO update package online, deobfuscated the XOR-inverted cabinet, decompiled `NCPTransportInterface.dll` using Ghidra, traced the I2C transport vtable, decoded the NCP frame format and chunked protocol, identified the async receive path, and wrote the script. Multiple sessions, each building on the last.
- **Reverse engineering tooling**: [Ghidra](https://ghidra-sre.org/) — the open-source software reverse engineering framework developed by the **NSA Research Directorate**
- **Original Windows tool**: `CalibG4.exe` by Sony/N-Trig, part of Sony VAIO Update package `EP0000601624.exe`
- **Community discovery**: Many Surface Pro 3 users on [surfaceforums.net](https://www.surfaceforums.net), Microsoft Answers, and GitHub Issues who identified `CalibG4.exe` as the fix and kept the knowledge alive

---

## License

The Python script (`ntrig_calib.py`) is released under the AGPLv3 License. See [LICENSE](./LICENSE) file.
