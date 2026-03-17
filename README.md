# ntrig-calib — Surface Pro 3 Touchscreen Dead Zone Fix (Linux)

> **TL;DR:** My first personal Claude aha moment. Bought a second-hand Microsoft Surface Pro 3, but the touchscreen was dead. Asked Claude for help. Claude diagnosed the firmware calibration issue, found the Windows fix (`CalibG4.exe`) online, deobfuscated the binary, reverse-engineered it with Ghidra, decoded the proprietary protocol, and wrote a working Python script — all without ever booting Windows. It fucking worked.

### See it in action

[![N-Trig touch screen re-calibration demo by René Rebe](https://img.youtube.com/vi/mVX-7ZI8ysk/0.jpg)](https://www.youtube.com/watch?v=mVX-7ZI8ysk)

*Dead zone visible at the bottom edge — fixed by software recalibration (video by [René Rebe](https://rene.rebe.de/2017-07-29/n-trig-touch-screens-occasionally-need-re-calibration/))*

**This tool was developed by Claude Opus 4.6 with extended thinking (Anthropic's AI assistant).** Given only a description of the symptom, Claude diagnosed the root cause, found the Sony VAIO update package online, deobfuscated its XOR-inverted cabinet archive, reverse engineered the proprietary NCP protocol from the extracted DLL using [Ghidra](https://ghidra-sre.org/), and wrote the working Python script across multiple sessions. Subsequent improvements and documentation updates may use different Claude models. The story is below.

---

## Table of Contents

- [The Problem](#the-problem)
- [Known Limitations and Unknowns](#known-limitations-and-unknowns)
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

## Known Limitations and Unknowns

**This script is based on reverse engineering and experimental findings:**
- The NCP response channel (report 0x06) has been observed in a single undocumented session but has **not been verified across multiple devices** or kernel versions.
- The exact conditions under which calibration succeeds or fails are not fully understood.
- Longevity varies: some units remain calibrated indefinitely, while others experience drift recurrence within days (see [Note on longevity](#note-on-longevity) below).
- Linux-specific behavior: the `direct-path` report IDs (0x29–0x2D) present in the Windows device tree are absent from the Linux HID descriptor, which may indicate different firmware behavior on Linux vs. Windows.

**If testing this script, please report your results** — both successes and failures — including your kernel version, device configuration, and diagnostic output. Community verification will help establish whether the script is reliably functional across different Surface Pro 3 units and configurations.

---

## The Fix

Running `CalibG4.exe` on Windows recalibrates the digitizer's firmware. Because the calibration is stored in the digitizer's own EEPROM — not in Windows — running it once from Windows fixes the dead zone on Linux too.

**This script does the same thing from Linux, without Windows.**

---

## The Full Story

### Background

The entire process — from diagnosis to working script — was driven by Claude Opus 4.6 with extended thinking. The user described the symptom (dead touch strip, pen still working). Claude identified it as a known N-Trig firmware calibration issue solvable in software, located `CalibG4.exe` inside a Sony VAIO update package online, deobfuscated the XOR-inverted cabinet, reverse engineered the proprietary NCP protocol from the DLL using [Ghidra](https://ghidra-sre.org/), and wrote the Python script across multiple sessions. The user's role was describing the problem and testing the result. All of the step-by-step technical descriptions below (reverse engineering stages, protocol analysis, diagnostics) were written by Claude—the user lacks the reverse-engineering and low-level protocol analysis skills required to document these discoveries. Subsequent improvements to the script or documentation may involve other Claude models.

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

The Linux-exposed HID report descriptor is **455 bytes** and defines **16 report IDs**: `0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0C, 0x11, 0x15, 0x18, 0x1B, 0x58`. The "direct path" reports `0x29–0x35` mentioned in Stage 4 are physically absent from this descriptor — they are absent from the firmware's native I2C-HID descriptor (see Stage 4 for the precise explanation of why they appear in the Windows device tree but not on Linux).

Early attempts sent NCP frames via `HIDIOCG/SFEATURE` on report 0x1B (259 bytes, the largest vendor report in the descriptor). SET_FEATURE would succeed (kernel accepted it) but the device never responded. A bug in the diagnostic probe loop (it tested buffer sizes `[8, 16, 34, 65, 260, 514]` in order and stopped at the first "success") caused 0x1B reads to stop at 8 bytes, returning only the truncated prefix `29 a9 19 9f 9a 19 a4 ...` — never the full 259-byte response. With a correct 260-byte buffer, the response contains 256 non-zero bytes. SET_FEATURE on 0x1B was re-tested in v4 with a correct 260-byte buffer and confirmed to only increment report 0x03 (the transaction counter), with no NCP response. The conclusion that 0x1B is not in the DLL's send path was later confirmed definitively by the Stage 4 DLL analysis. Report 0x03 changed on every write, which looked promising but turned out to be just a HID transaction counter incrementing.

Raw I2C access also failed — unbinding the `i2c_hid_acpi` driver powers the device down, and it wouldn't respond to I2C commands afterward.

The kernel's `hid-ntrig.c` driver (sometimes mentioned in Surface forums) is irrelevant here: it includes `<linux/usb.h>`, calls `usb_control_msg()`, and matches only `HID_USB_DEVICE(...)` — it cannot bind to an I2C-HID device. On kernel 6.11+, [HID-BPF](https://docs.kernel.org/hid/hid-bpf.html) would be a cleaner alternative approach — it can inject initialization commands without unbinding any driver.

**Stage 4 — Deep DLL analysis (v4–v5, the breakthrough at v5)**

Claude disassembled the I2C transport class's vtable in Ghidra and traced the full call graph. The DLL contains **two separate transport classes**, not alternative branches in one function:

- **I2C transport** (`0x180008730`, object size `0xB8` bytes): send function `0x1800088C0` forks on capability flag `[this+0x7c]`:
  ```
  if [this+0x7c] != 0:
      → CHUNKED PATH: report 0x05, 61-byte chunks
  else:
      → DIRECT PATH: size-based report ID (0x29–0x2D)
  ```
  The flag `[this+0x7c]` is **set by the capability probe sequence** (function `0x1800095d0`), which tests probes against three configurations (cmd_ids `0x01`, `0x0B`, `0x0C`). On the SP3 running Windows, the `0x0C` probe presumably succeeds, enabling chunked mode; on Linux (where direct-path collections are absent), the device automatically uses the chunked protocol. The 8 extra bytes relative to the USB transport (at offsets `[this+0x70]`–`[this+0x7f]`) hold the chunked-path state, including the `[this+0x7c]` flag.

- **USB transport** (`0x180001280`, object size `0xB0` bytes): uses a separate size-based report ID table (0x2E–0x35). DLL dynamically loads `winusb.dll` at runtime, suggesting support for USB-attached N-Trig dongles in addition to I2C-HID.

The I2C direct-path report IDs are chosen by a **size table** (function `0x1800011b0`): `<16B→0x29`, `<32B→0x2A`, `<63B→0x2B`, `<255B→0x2C`, `<511B→0x2D`. The USB table similarly: `≤17B→0x2E`, `≤33B→0x2F`, `≤64B→0x30`, `≤256B→0x31`, `≤512B→0x32`, `≤4096B→0x35`, `≤8192B→0x34`.

The flag `[this+0x7c]` is set by the **capability probe sequence** (function `0x1800095d0`, which uses `SetupDi` enumeration + `HidP_GetCaps` and stores the result in `[this+0x30]`). The DLL probes against three configurations (cmd_ids `0x01`, `0x0B`, `0x0C`):
- On Windows, the `0x0C` probe presumably succeeds, and the flag `[this+0x7c]` is set to enable chunked mode.
- On Linux, those direct-path report IDs do not exist (see below), so the probe fails, the flag remains unset, and the DLL defaults to chunked protocol.

This is **software-driven fallback logic**, not automatic hardware behavior. The fallback occurs because the **capability probe fails to detect direct-path support on Linux**, triggering the DLL's code path decision at the flag level.

Report **0x05 is write-only**: `GET_FEATURE` on 0x05 returns no response — confirmed empirically by the diagnostic script.

The "direct path" reports (0x29–0x2D for I2C, 0x2E–0x35 for USB-HID) map to **separate HID collections** — distinct PDOs in the Windows HID device tree. Windows HID minidrivers can inject additional HID collections (with those report IDs) into the descriptor before `HIDClass.sys` parses it, which is why those collection PDOs exist in the Windows device tree but are **absent from the firmware's native I2C-HID descriptor on Linux**. This is also why the Linux-exposed `/dev/hidrawN` device only shows the 16 base report IDs and never the 0x29–0x35 range. The v5 diagnostic script probed these report IDs directly on the device and received no response, empirically confirming their absence.

**When the DLL's capability probe (function `0x1800095d0`) fails to detect these direct-path report IDs on Linux**, it sets the fallback flag `[this+0x7c]`, causing the send function (`0x1800088C0`) to use the chunked protocol via report 0x05 instead. This is the DLL's deliberate software response to missing capabilities, not a hardware-level automatic behavior. **As a result, on Linux the DLL falls back to the chunked protocol via report 0x05** — a probe-driven decision, not automatic hardware automation.

**The chunked protocol is the PRIMARY I2C send path** (function `0x18000CC80`):
```
Each HID write = 61 bytes:
  [0x05] [remaining_chunks] [59 bytes of NCP frame data]

remaining_chunks counts DOWN: last chunk = 0
```

For a 15-byte NCP frame (no payload), this is a single 61-byte write:
```
[0x05] [0x00] [7e 01 00 0f 00 01 20 0a 00 00 00 00 00 47] [zeros to pad to 61]
```

This is the expected and reliable path for I2C-HID devices. The direct-path report IDs (0x29–0x2D) are present only in Windows via injected HID collections; on Linux they are absent, so the device automatically falls back to the chunked protocol.

**Stage 5 — The async response (EXPERIMENTAL)**

Another key finding: the DLL's receive thread uses `ReadFile` on the HID device handle (async I/O), **not** `HidD_GetFeature`. On Linux this maps to a non-blocking `read()` on the hidraw fd. Previous script versions were polling GET_FEATURE, completely missing the responses. The v5 script uses `select()` + `read()` to capture the NCP response.

During DLL analysis, the capability probe sequence (function `0x1800095d0`) identifies three probe configurations with cmd_ids **0x01, 0x0B, and 0x0C**. Based on this analysis and the DLL's feature detection logic, **0x0B and 0x0C emerged as candidate response channels** from the reverse-engineered code structure.

However, empirical testing on a single Surface Pro 3 device revealed that NCP responses were actually received on **report 0x06** in an undocumented testing session — a finding that resulted in successful recalibration on that system. This observation is **not supported by chat-based reverse engineering evidence** and comes from a single unlogged session.

**⚠️ CRITICAL DISTINCTION — Verified candidates vs. unverified observation:**
- **0x0B and 0x0C** are candidate response channels **identified from structured DLL analysis** (the capability probes). These are the most likely based on the reverse-engineered code.
- **0x06** is an **empirical observation from a single undocumented session only**. It has **NOT been independently verified** across multiple devices, kernel versions, or configurations, and lacks supporting chat-based evidence. **Do not rely on 0x06 as the definitive response channel.** The actual response channel on your device may be 0x0B, 0x0C, or something else entirely.

If you run the script and it works on your system, the response channel it detected is what works for your configuration. If you run the script and it fails or reports no responses, the response channel may differ on your kernel version or device variant.

```
Observed example from undocumented session (report 0x06):
Input report 0x06: 7e 01 00 12 00 81 20 0b 00 00 00 00 00 00 21 21 21 60 ...
                   ^^                    ^^
                   NCP marker            cmd_group=0x20, cmd_id=0x0B (GET_STATUS response)
                                                                      ^^^^^^^^^^^
                                                                      payload = "!!!" = unknown/intermediate state
```

The `0x81` in the flags byte (bit 7 set) indicates a **response frame**. The `!!!` payload maps to the string `"Unknown status, waiting"` in `CalibG4.exe` — it is an intermediate polling state. The DLL continues polling after receiving it.

**Further testing on additional Surface Pro 3 units is critical** to establish whether 0x06 is a reliable response channel, whether the actual channel is 0x0B or 0x0C (the reverse-engineered candidates), or whether it is device-specific or kernel-version-dependent behavior. Community verification will clarify this ambiguity.

**Full CalibG4 call sequence (from Ghidra, main sequence at `0x1400010B0`):**
- Read buffer: 4096 bytes
- `START_CALIB` timeout: 3000 ms
- Status poll loop: up to 60 iterations × 500 ms = 30 seconds max
- Explicit `DeInit` + `Deregister` cleanup at end
- `Init` accepts an IP address and port (for remote calibration over a network — "Calib on local/remote machine" per PDB strings)

VID/PID matching is **dynamic**: the caller passes VID/PID (or `-1` for "any") to the enumeration function at `0x180003078`. The DLL does not hardcode `0x1B96`.

**Binary metadata:** PDB path `D:\Jenkins\workspace\G4_Host\Off_G4_Host_BUILD\Host_Win\H_Win_Tools\CalibG4\x64\Release\CalibG4.pdb`, version 1.0.0.12.

### Summary of failed approaches (for future reference)

| Approach | Why it failed |
|---|---|
| Sending NCP to report 0x1B | Wrong report. 0x1B was believed to be the NCP channel through v4 — ruled out only by the Stage 4 deep DLL vtable analysis (v5). A buffer-size bug (stopped at 8 bytes) made the read appear stable (`29 a9 19 9f ...`); retested at 260 bytes in v4: returns 256 non-zero bytes of static device state data, confirmed not an NCP response. |
| Raw I2C after unbinding driver | `i2c_hid_acpi` (device at `INT33C3:00`, bus 1, slave 0x07) powers the device down on unbind; all subsequent I2C commands fail with `EREMOTEIO` (errno 121). |
| Polling GET_FEATURE for responses | The DLL uses async ReadFile, not GetFeature. Responses come as input reports. |
| iptsd / linux-surface kernel | Completely wrong technology. SP3 doesn't use IPTS. |
| Report 0x03 changes as signal | It's a transaction counter, not an NCP response. |

---

## Usage

```bash
# Requires root (hidraw access)
sudo python3 ntrig_calib.py                  # run full diagnostics (default)
sudo python3 ntrig_calib.py --diag           # same as above, explicit
sudo python3 ntrig_calib.py --calibrate      # send START_CALIB only
sudo python3 ntrig_calib.py --list           # list all N-Trig hidraw devices
sudo python3 ntrig_calib.py -d /dev/hidraw1  # specify device explicitly
sudo python3 ntrig_calib.py --module-id 0x0002  # override NCP module ID (default: 0x0001)
```

**By default (and with `--diag`), the script runs diagnostics**, not a silent calibration. It will:
1. Auto-detect the N-Trig hidraw device (or use `-d`)
2. Parse and print the HID report descriptor
3. Take a baseline GET_FEATURE snapshot of all reports
4. Probe undeclared reports 0x29–0x2D (these are absent on I2C-HID devices; only USB transports have the direct-path report IDs)
5. Send NCP GET_STATUS and START_CALIB via the chunked report 0x05 protocol
6. Attempt direct (non-chunked) NCP via report 0x05
7. Try async input report reads after each send, looking for NCP responses

Use `--calibrate` to send only the START_CALIB command, skipping the diagnostic probing — recommended once you have confirmed from a `--diag` run that the NCP channel is responding. **Important:** The response channel confirmation in this script is based on empirical observations from an undocumented session and **has not been verified across multiple devices**. Your kernel version or device configuration may differ; always verify with `--diag` first to ensure the NCP channel is responding on your system before running `--calibrate`.

**After running, touch the previously-dead area.** It should respond immediately. No reboot required.

**⚠️ Testing advisory:** This script is based on reverse engineering and experimental findings. Test cautiously on a non-critical device first. If the script successfully recalibrates your touchscreen, or if it fails to respond, please report your results (including device model, kernel version, and output from `--diag`) to help verify the script's reliability across different configurations.

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
- The N-Trig device at `/dev/hidraw*` (verify with `dmesg | grep -i "NTRG\|1B96\|multitouch"` or `ls /dev/hidraw*`)
- Kernel with `hid-multitouch` bound to `NTRG0001:01 1B96:1B05` (standard on any Linux distribution with kernel 4.8 or later)


---

## Credits

- **Diagnosis, reverse engineering, and script**: Done primarily by [Claude Opus 4.6](https://claude.ai) with extended thinking (Anthropic). Given only a symptom description, Claude identified the calibration drift root cause, located the Sony VAIO update package online, deobfuscated the XOR-inverted cabinet, decompiled `NCPTransportInterface.dll` using Ghidra, traced the I2C transport vtable, decoded the NCP frame format and chunked protocol, identified the async receive path, and wrote the script. Subsequent improvements and documentation refinements may involve other Claude models.
- **Reverse engineering tooling**: [Ghidra](https://ghidra-sre.org/) — the open-source software reverse engineering framework developed by the **NSA Research Directorate**
- **Original Windows tool**: `CalibG4.exe` by Sony/N-Trig, part of Sony VAIO Update package `EP0000601624.exe`
- **Community discovery**: Many Surface Pro 3 users on [surfaceforums.net](https://www.surfaceforums.net), Microsoft Answers, and GitHub Issues who identified `CalibG4.exe` as the fix and kept the knowledge alive

---

## Related Issues

This project addresses dead zone issues reported across multiple Surface devices and platforms:

- [Surface Pro 3 touchscreen problem](https://www.reddit.com/r/Surface/comments/2knsyd/surface_pro_3_touchscreen_problem/) (Reddit)
- [Bottom part of touch screen responds to pen but not touch](https://www.reddit.com/r/Surface/comments/1b1hdc9/bottom_part_of_touch_screen_responds_to_pen_but/) (Reddit)
- [SurfaceBook 3 screen dead zones](https://www.reddit.com/r/Surface/comments/180v7xl/surfacebook_3_screen_dead_zones/) (Reddit)
- [iptsd issue #202 — N-Trig calibration discussion](https://github.com/linux-surface/iptsd/issues/202) (GitHub)

---

## License

The Python script (`ntrig_calib.py`) is released under the AGPLv3 License. See [LICENSE](./LICENSE) file.
