# ntrig-calib — Surface Pro 3 Touchscreen Dead Zone Fix (Linux)

> **TL;DR:** The Surface Pro 3's N-Trig digitizer develops a dead rectangular strip over time due to firmware calibration drift. On Windows, Sony/N-Trig's `CalibG4.exe` fixes it in seconds. This repo contains a Python script that replicates what `CalibG4.exe` does — over Linux's `/dev/hidraw` interface — so you never need to boot Windows.

**This tool was developed entirely through reverse engineering with the help of Claude (Anthropic's AI assistant), which single-handedly decompiled, traced, and decoded the proprietary NCP protocol from a Sony VAIO update binary.** The story is below.

---

## The Problem

Surface Pro 3 units develop a **dead horizontal or vertical strip** on their touchscreen where touch input is completely unresponsive. Pen input still works in the dead zone. The strip survives reboots, driver reinstalls, and kernel upgrades.

**This is not a driver bug.** It is firmware-level calibration drift in the N-Trig DuoSense digitizer. The calibration data lives in the digitizer's own non-volatile memory and degrades over time, independently of the OS.

### How to confirm you have this issue

```bash
# Should show zero events when you touch the dead zone
sudo evtest   # pick the N-Trig touchscreen device

# Pen working in dead zone + touch dead = calibration drift (this tool fixes it)
# Pen ALSO dead = hardware failure (this tool won't help)

# If dead strip appears in the UEFI touch test (Volume Up + Power at boot),
# it's definitely below OS level — calibration drift confirmed.
```

---

## Why iptsd / linux-surface won't help

The Surface Pro 3 does **not** use Intel Precise Touch & Stylus (IPTS). That was introduced with the Surface Pro 4. The SP3 uses a completely different digitizer:

| | Surface Pro 3 | Surface Pro 4+ |
|---|---|---|
| Digitizer | N-Trig DuoSense | Intel IPTS |
| HID ID | `NTRG0001:01 1B96:1B05` | `045E:xxxx` via MEI |
| Bus | I2C-HID (bus type 0x18) | MEI (Management Engine) |
| Kernel driver | `hid-multitouch` (mainline) | `ipts` kernel module |
| Userspace daemon | None needed | `iptsd` required |

The SP3 touchscreen has worked out-of-the-box on mainline Linux since kernel 4.8 (2016). `iptsd` is completely irrelevant to it.

---

## The Fix

Running `CalibG4.exe` on Windows recalibrates the digitizer's firmware. Because the calibration is stored in the digitizer's own EEPROM — not in Windows — running it once from Windows fixes the dead zone on Linux too.

**This script does the same thing from Linux, without Windows.**

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

### Requirements

- Python 3.6+
- Root privileges
- The N-Trig device at `/dev/hidraw*` (verify with `lsusb -t` or `ls /dev/hidraw*`)
- Kernel with `hid-multitouch` bound to `NTRG0001:01 1B96:1B05` (standard on Ubuntu 20.04+)

---

## How It Works — The Full Story

### Background

The Windows fix (`CalibG4.exe`) communicates with the N-Trig digitizer via a proprietary binary protocol called **NCP (N-Trig Communication Protocol)**. The tool is distributed only as part of a Sony VAIO driver update package, and no documentation or Linux equivalent has ever existed publicly.

### Finding the tool

The binary `CalibG4.exe` is distributed inside `EP0000601624.exe`, a Sony VAIO Update self-extracting wrapper. It's available from Sony's support pages and has been referenced in Surface forums for years as the fix for SP3 dead zones ([example thread](https://answers.microsoft.com/en-us/surface/forum/all/does-anyone-still-have-the-calibg4exe-touch-screen/eb5376c3-1e59-474a-80df-00f918c8f9a6)).

Extracting it isn't obvious: the wrapper embeds a cabinet file that is **XOR-inverted** (each byte XORed with 0xFF) inside a PE resource blob. Once you invert the bytes and `cabextract`, you get:
- `CalibG4.exe` (19 KB) — the calibration tool
- `NCPTransportInterface.dll` (151 KB) — the HID communication layer

### Reverse engineering the DLL

This is where Claude came in. The goal was to understand exactly what bytes to send over the Linux hidraw interface.

**Stage 1 — Initial analysis** revealed:
- `CalibG4.exe` sends two NCP commands: `START_CALIB` (group=0x20, id=0x0A) and polls `GET_STATUS` (group=0x20, id=0x0B)
- Status responses: `\x42\x42\x42` ("BBB") = complete, `\x63\x63\x63` ("ccc") = in progress, `\x21\x21\x21` ("!!!") = waiting
- All actual communication goes through `NCPTransportInterface.dll`

**Stage 2 — NCP frame format** (from disassembly of `0x18000D0D0`):

```
Byte  0:     0x7E  — start marker
Bytes 1-2:   Module ID (LE uint16)
Bytes 3-4:   Total frame size (LE uint16) = 14 + payload_len + 1
Byte  5:     Flags: 0x01 = expects response, 0x41 = fire-and-forget
Byte  6:     Command group (0x20 = calibration)
Byte  7:     Command ID (0x0A = start, 0x0B = get status)
Bytes 8-11:  Sequence number (LE uint32)
Bytes 12-13: Reserved (0x00)
Bytes 14..:  Payload
Last byte:   Checksum = (-sum(signed_bytes)) & 0xFF
```

**Stage 3 — The hidraw dead end (v1–v3)**

Early attempts sent NCP frames via `HIDIOCG/SFEATURE` on report 0x1B (259 bytes, the largest vendor report in the descriptor). SET_FEATURE would succeed (kernel accepted it) but the device never responded. Report 0x03 changed on every write, which looked promising but turned out to be just a HID transaction counter incrementing.

Raw I2C access also failed — unbinding the `i2c_hid_acpi` driver powers the device down, and it wouldn't respond to I2C commands afterward.

**Stage 4 — Deep DLL analysis (v4–v5, the breakthrough)**

Claude disassembled the I2C transport class's vtable and traced the full call graph. The key discovery was a **fork in the send function** (`0x1800088C0`) based on a capability flag `[this+0x7c]`:

```
if [this+0x7c] != 0:
    → CHUNKED PATH: report 0x05, 61-byte chunks
else:
    → DIRECT PATH: reports 0x29–0x2D (hardcoded, don't exist on Linux)
```

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

Another key finding: the DLL's receive thread uses `ReadFile` on the HID device handle (async I/O), **not** `HidD_GetFeature`. On Linux this maps to a non-blocking `read()` on the hidraw fd. Previous script versions were polling GET_FEATURE, completely missing the responses. The v5 script uses `select()` + `read()` and immediately captures the NCP response:

```
Input report 0x06: 7e 01 00 12 00 81 20 0b 00 00 00 00 00 00 21 21 21 60 ...
                   ^^                    ^^
                   NCP marker            cmd_group=0x20, cmd_id=0x0B (GET_STATUS response)
                                                                      ^^^^^^^^^^^
                                                                      payload = "!!!" = calibration triggered
```

The `0x81` in the flags byte (bit 7 set) indicates a **response frame**. The `!!!` status means the calibration is running. The screen was fully fixed after this ran.

### Summary of failed approaches (for future reference)

| Approach | Why it failed |
|---|---|
| Sending NCP to report 0x1B | Wrong report. 0x1B is a crypto/auth state register, silently ignores writes. |
| Raw I2C after unbinding driver | `i2c_hid_acpi` powers device down on unbind; EREMOTEIO on all commands. |
| Polling GET_FEATURE for responses | The DLL uses async ReadFile, not GetFeature. Responses come as input reports. |
| iptsd / linux-surface kernel | Completely wrong technology. SP3 doesn't use IPTS. |
| Report 0x03 changes as signal | It's a transaction counter, not an NCP response. |

---

## Files in This Repository

| File | Description |
|---|---|
| `ntrig_calib.py` | The Linux calibration script |
| `README.md` | This file |

---

## Credits

- **Reverse engineering and script**: Developed entirely with [Claude](https://claude.ai) (Anthropic), which decompiled `NCPTransportInterface.dll`, traced the I2C transport vtable, decoded the NCP frame format and chunked protocol, and identified the async receive path. Multiple sessions, each building on the last.
- **Original Windows tool**: `CalibG4.exe` by Sony/N-Trig, part of Sony VAIO Update package `EP0000601624.exe`
- **Community discovery**: Many Surface Pro 3 users on [surfaceforums.net](https://www.surfaceforums.net), Microsoft Answers, and GitHub Issues who identified `CalibG4.exe` as the fix and kept the knowledge alive

---

## Related Issues

This tool addresses the same problem discussed in:
- *(add links to github issues here)*

---

## License

The Python script (`ntrig_calib.py`) is released under the AGPLv3 License. See [LICENSE](./LICENSE) file.
