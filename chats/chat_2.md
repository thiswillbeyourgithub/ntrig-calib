https://claude.ai/chat/0f175d5a-829c-4c20-aea4-9be1f99b2b92
<me>
Here is the handoff document created by the previous claude chat so that you are roughly up to speed:

---
Here are the three key files. A few things I want to highlight about the handoff document that are easy to miss:
The single most important next action is fixing the buffer size probe. The diagnostic loop checks sizes `[8, 16, 34, 65, 260, 514]` in order and stops at the first success. Report 0x1B succeeds at size 8, so it never tries 260. The next Claude instance should try reading 0x1B at 260 bytes directly — that alone might reveal a full NCP response we've been missing all along.
The second priority is fixing `find_i2c_device()` for raw I2C. Your sysfs shows `i2c-NTRG0001:01`, not `1-0007`. The function's regex only matches `bus-addr` format. The fix needs to parse the ACPI-named entry to extract the actual I2C bus number and slave address (probably from the `uevent` file or parent symlink).
My biggest honest uncertainty: I don't actually know whether the NCP frame should go directly into report 0x1B's payload starting at byte 1, or whether there's a sub-header or wrapper specific to the I2C-HID variant. The DLL's `HidP_GetCaps` path dynamically discovers this, and deeper Ghidra analysis of how it handles non-USB transports would clarify. Alternatively, one raw I2C capture from a Windows boot running CalibG4 would settle everything instantly.
The extracted `CalibG4.exe` (19KB) and `NCPTransportInterface.dll` (151KB) are the real binaries ready for Ghidra — the next session should focus decompilation on the DLL's `Init()` → `Register()` → `SendRequest()` path specifically tracing how it constructs the HID feature report buffer for a device with a 259-byte feature report.

Handoff document:

Surface Pro 3 N-Trig Touchscreen Dead Strip Fix — Complete Handoff Document
Date: March 2026
Context: Linux-only user (Ubuntu 24.04 LTS), psychiatry resident & data scientist, bilingual FR/EN
1. THE PROBLEM
The user has a second-hand Surface Pro 3 with a dead rectangular strip on the touchscreen — an entire band where touch input is not recognized at all (confirmed with `xev` and `evtest`). There are no relevant logs in `journalctl`. The user has already tried:
* The standard Ubuntu 24.04 kernel
* The linux-surface kernel (https://github.com/linux-surface/linux-surface)
* Recompiling recent AND old versions of `iptsd` (Intel Precise Touch & Stylus daemon)
None of these helped, and this is expected — see section 2.
2. CRITICAL FINDING: THE SP3 DOES NOT USE IPTS
This was the first major discovery. The Surface Pro 3 does NOT use Intel Precise Touch & Stylus (IPTS). IPTS was introduced with the Surface Pro 4. The SP3 uses a completely different touch technology:
Surface Pro 3 Surface Pro 4+ Digitizer N-Trig DuoSense Intel IPTS HID ID `NTRG0001:01 1B96:1B05` Various `045E:xxxx` via MEI Bus I2C-HID (bus type 0x18) MEI (Management Engine) Kernel driver `hid-multitouch` (mainline) `ipts` kernel module Userspace daemon None needed `iptsd` required
Therefore iptsd is completely irrelevant to the SP3. Stop using it.
The SP3's touchscreen has worked out-of-the-box on mainline Linux since kernel 4.8 (2016). The `hid-multitouch` driver binds to `NTRG0001:01 1B96:1B05`.
3. ROOT CAUSE: N-TRIG CALIBRATION DRIFT
The dead strip is an extremely well-documented N-Trig firmware calibration drift problem. The N-Trig digitizer stores calibration data in its own non-volatile EEPROM, and this calibration degrades over time.
Diagnostic confirmation tests (user should run these):
1. `sudo evtest` → select NTRG touchscreen → touch dead zone → if zero events, it's firmware-level
2. Pen vs touch in dead zone: pen working + touch dead = calibration drift (fixable)
3. UEFI touch test (Volume Up + Power): if dead strip exists in UEFI, it's below OS level
4. `libinput list-devices | grep -A5 "NTRG"` to verify driver binding
The fix on Windows is `CalibG4.exe`, a Sony/N-Trig calibration tool that writes corrected calibration data to the digitizer's EEPROM via HID feature reports. Because calibration lives in firmware, running it from Windows fixes the dead strip on Linux too.
Sources:
* https://gartnertechnology.com/microsoft-surface-pro-3-dead-zone-fixed/
* https://answers.microsoft.com/en-us/surface/forum/all/does-anyone-still-have-the-calibg4exe-touch-screen/eb5376c3-1e59-474a-80df-00f918c8f9a6
* https://rene.rebe.de/2017-07-29/n-trig-touch-screens-occasionally-need-re-calibration/
* https://thedigitallifestyle.com/w/2018/01/fix-non-responsive-areas-surface-pro-touch-screen/
* Multiple surfaceforums.net threads about dead zones
4. REVERSE ENGINEERING CalibG4.exe
The user uploaded `EP0000601624.exe` (1.9MB), a Sony VAIO Update self-extracting wrapper.
4.1 Extraction process
The wrapper is a PE32 MFC application (Sony Packaging Manager). It imports only `KERNEL32.dll` and `ADVAPI32.dll` — it's purely an extractor/launcher.
The payload was found inside an `[0]` resource extracted by 7z. The resource contains a config header (UTF-16LE) followed by a bitwise-inverted (XOR 0xFF) Microsoft Cabinet file. The key discovery:

```python
data = open('payload_blob.bin', 'rb').read()
inverted = bytes(b ^ 0xFF for b in data)
# inverted starts with b'MSCF' — a valid Microsoft Cabinet!

```

After inversion and `cabextract`, two files were extracted:
1. `CalibG4.exe` — 19,456 bytes, PE32+ (x64), the actual calibration tool
2. `NCPTransportInterface.dll` — 151,040 bytes, PE32+ DLL, the HID communication layer
Both files are saved and available for further analysis.
4.2 CalibG4.exe analysis
PDB path: `D:\Jenkins\workspace\G4_Host\Off_G4_Host_BUILD\Host_Win\H_Win_Tools\CalibG4\x64\Release\CalibG4.pdb` Version: 1.0.0.12 Description: "Panel calibration tool" / "Calib on local/remote machine"
Imports from NCPTransportInterface.dll (C++ mangled):
* `CNCPTransportInterface::CNCPTransportInterface()` — constructor
* `CNCPTransportInterface::Init()` — local init (no args)
* `CNCPTransportInterface::Init(const char*, int)` — remote init (IP, port)
* `CNCPTransportInterface::Register(unsigned long)` — register client module
* `CNCPTransportInterface::SendRequest(void*, byte, byte, void*, uint, byte, void*, uint*, ulong)` — send NCP command
* `CNCPTransportInterface::DeInit()` — cleanup
* `CNCPTransportInterface::Deregister(void*)` — unregister
Calibration sequence (from disassembly at 0x1400010B0):
1. Construct `CNCPTransportInterface`, call `Init()` (local) or `Init(ip, port)` (remote)
2. Call `Register(0)` — returns a handle
3. Call `SendRequest` with: cmd_group=0x20, cmd_id=0x0A (START_CALIB)
   * Parameters: r8b=0x20, r9b=0x0A, buffer=4096 bytes, timeout=3000ms
4. Print "Please do not touch the screen..."
5. Poll loop (up to 60 iterations, 500ms sleep via `Sleep(0x1F4)`):
   * Call `SendRequest` with: cmd_group=0x20, cmd_id=0x0B (GET_STATUS)
   * Parse response first 3 bytes:
      * `\x42\x42\x42` ("BBB") → complete, exit with code 2
      * `\x63\x63\x63` ("ccc") → in progress, print "In progress"
      * `\x21\x21\x21` ("!!!") → unknown, print "Unknown status, waiting"
6. DeInit, Deregister, free buffer
String table (from .rdata):
* 0x32F0: "Failed to init transport, status="
* 0x3318: "Failed send NCP command="
* 0x3338: "Please do not touch the screen..."
* 0x3360: "Unknown status, waiting" (maps to "!!!")
* 0x3378: "In progress" (maps to "ccc")
4.3 NCPTransportInterface.dll analysis
Imports: `HID.DLL`, `SETUPAPI.dll`, `KERNEL32.dll`, `USER32.dll`, `ADVAPI32.dll`, `RPCRT4.dll`, `MSVCP100.dll`, `MSVCR100.dll`, plus dynamic load of `winusb.dll`
Key HID API usage:
* `SetupDiGetClassDevsW` / `SetupDiEnumDeviceInterfaces` / `SetupDiGetDeviceInterfaceDetailW` — device enumeration
* `HidD_GetAttributes` — gets VID/PID (comparison at 0x180003078-0x1800030AC)
* `HidD_GetPreparsedData` / `HidP_GetCaps` — discovers report sizes dynamically
* `HidD_SetFeature` — sends NCP frames (calls at 0x18000902A, 0x18000CD9D, 0x18000CE46)
* `HidD_GetFeature` — reads NCP responses (call at 0x18000B253)
* `CreateFileW` — opens HID device handle
* `ReadFile` — reads async input reports
VID/PID handling: The DLL does NOT hardcode 0x1B96. Instead, the caller passes VID/PID (or -1 for "any") and the enumeration code at 0x180003078 compares `HIDD_ATTRIBUTES.VendorID` and `ProductID` against these parameters.
NCP Frame Format (from frame builder at 0x18000D0D0):

```
Byte  0:     0x7E — start marker
Bytes 1-2:   Module ID (LE uint16) — arbitrary session identifier
Bytes 3-4:   Total frame size (LE uint16) = header(14) + payload + checksum(1)
Byte  5:     Flags: 0x01 = request expecting response, 0x41 = no response
Byte  6:     Command group (e.g., 0x20 for calibration)
Byte  7:     Command ID (e.g., 0x0A=start, 0x0B=status)
Bytes 8-11:  Sequence number (LE uint32, usually 0)
Byte  12:    Reserved (0)
Byte  13:    Reserved (0)
Bytes 14..:  Payload data
Last byte:   Checksum

```

Checksum algorithm (from 0x18000DC20):

```python
def ncp_checksum(frame_bytes):
    s = sum((b if b < 128 else b - 256) for b in frame_bytes)
    return (-s) & 0xFF

```

It's the two's complement of the signed byte sum. The sum of all bytes (including checksum) equals 0 mod 256.
Feature report ID selection (from 0x18000DDC0): This is a SIZE-TO-REPORT-ID mapper. On USB-HID, the N-Trig device exposes multiple feature reports of different sizes. The DLL picks the smallest report that fits the NCP frame:
Report ID Max NCP frame size 0x2E ≤ 17 bytes 0x2F ≤ 33 bytes 0x30 ≤ 64 bytes 0x31 ≤ 256 bytes 0x32 ≤ 512 bytes 0x35 ≤ 4096 bytes 0x34 ≤ 8192 bytes
CRITICAL: These report IDs (0x2E-0x35) are for USB-HID N-Trig devices. They do NOT exist in the SP3's I2C-HID report descriptor. The DLL uses `HidP_GetCaps` to dynamically discover available report sizes, so on Windows with I2C-HID, it would use different report IDs.
5. THE I2C-HID PROBLEM
5.1 Device configuration on the SP3

```
/dev/hidraw1: VID=0x1B96 PID=0x1B05 bus=24 (I2C-HID)
I2C device: i2c-NTRG0001:01 (under /sys/bus/i2c/devices/)

```

The HID report descriptor is 455 bytes and contains these report IDs: `0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0C, 0x11, 0x15, 0x18, 0x1B, 0x58`
Report IDs 0x2E-0x35 do NOT exist in this descriptor.
5.2 Feature report probe results
Only 3 feature reports respond to GET_FEATURE via hidraw:
Report ID Buffer size Response Notes 0x03 8 `03 87 f5 e4 0f 00 b8 04` Has data 0x04 8 `04 0a 00 00 00 00 00 00` Has data 0x1B 8 `1b 29 a9 19 9f 9a 19 a4` Has data — but truncated!
All other report IDs (0x05, 0x07, 0x0A, 0x0B, 0x0C, 0x11, 0x15, 0x18, 0x58) returned no response at any tested buffer size (8, 16, 34, 65, 260, 514).
5.3 The 0x1B truncation issue
Report 0x1B is 259 bytes according to the HID descriptor (1 byte report ID + 258 bytes data). However, the diagnostic script only got 8 bytes back. This is because:
1. The script tries buffer sizes [8, 16, 34, 65, 260, 514] in order and stops at the first success
2. The kernel's `i2c-hid` driver may truncate the response or the `HIDIOCGFEATURE` ioctl may succeed with a too-small buffer, returning only partial data
The 8 bytes `1b 29 a9 19 9f 9a 19 a4` match exactly the first 8 bytes of the full 259-byte response seen in kernel debug logs from Benjamin Tissoires' 2015 analysis of the SP3 I2C-HID device.
THIS IS VERY LIKELY THE NCP CHANNEL. The response needs to be read with a 260-byte buffer.
5.4 SET_FEATURE results
SET_FEATURE succeeded for report 0x1B with NCP frames embedded. However, the response read was still the same 8 bytes. This could mean:
1. The NCP frame was sent but the response needs a larger read buffer
2. The NCP frame was silently ignored because it was in the wrong format for I2C-HID
3. The device needs an initialization handshake before accepting NCP commands
5.5 The i2c-hid kernel validation issue
The `i2c-hid-core.c` driver contains a post-response validation check:

```c
if (report_id && recv_len != 0 && recv_buf[0] != report_id)
    return -EINVAL;

```

If you request report X but the device responds with report Y in byte 0, the kernel returns `-EINVAL`. This is documented in kernel mailing list discussions about the SP3 specifically.
5.6 The raw I2C approach
The script's `--raw-i2c` mode failed because `find_i2c_device()` only matched entries like `1-0007` (bus-address format) but the SP3's device appears as `i2c-NTRG0001:01` in sysfs. This is a bug in the script's device discovery. The fix is to also match non-numeric-prefix entries and extract the bus number from the device's `i2c-N` parent.
The I2C device sysfs entries are:

```
i2c-0/  i2c-1/  i2c-2/  ... i2c-11/  i2c-MSHW0030:00/  i2c-NTRG0001:01/

```

For raw I2C access, you need:
1. The I2C bus number (check `i2c-NTRG0001:01/` → look at what bus it's on)
2. The slave address (typically 0x07 for SP3 N-Trig, but verify from sysfs)
3. Unbind the `i2c_hid_acpi` driver first
4. Load `i2c-dev` module
5. Use I2C_RDWR ioctl for write-then-read transactions
The I2C-HID protocol registers (standard defaults, verify from HID descriptor at register 0x0001):
* Command register: 0x0005
* Data register: 0x0006
* Report descriptor register: found in HID descriptor
6. WHAT'S BEEN TRIED AND WHAT HASN'T
Tried:
* ✅ Extracted CalibG4.exe and NCPTransportInterface.dll from the Sony wrapper
* ✅ Reverse-engineered the NCP frame format (marker, module ID, size, flags, cmd group, cmd id, sequence, checksum)
* ✅ Identified calibration commands: START=0x20/0x0A, STATUS=0x20/0x0B
* ✅ Identified status response patterns: BBB=done, ccc=progress, !!!=waiting
* ✅ Sent NCP frames via SET_FEATURE on report 0x1B through hidraw — SET succeeds
* ✅ Read response via GET_FEATURE on report 0x1B — but only 8 bytes returned
Not yet tried:
* ❌ Reading report 0x1B with a proper 260-byte buffer (the probing tried 260 but stopped at 8 because it checked 8 first — need to fix the probe order for 0x1B specifically)
* ❌ Raw I2C access (script bug: couldn't find device because of sysfs naming)
* ❌ Full HID report descriptor parsing (the raw descriptor is available at 455 bytes)
* ❌ Checking whether the NCP frame needs to fill the entire 258-byte payload (zero-padded)
* ❌ Trying the hidraw path with report 0x1B at EXACTLY 260 bytes for both SET and GET
* ❌ Sniffing I2C bus traffic during calibration attempts to see what actually goes on the wire
* ❌ Deeper Ghidra analysis of how the DLL handles I2C-HID specifically (it supports both USB and I2C — look for WinUSB vs HID code paths)
7. RECOMMENDED NEXT STEPS (in priority order)
7.1 Fix the buffer size issue (HIGHEST PRIORITY)
The probe loop tries sizes [8, 16, 34, 65, 260, 514] and stops at first success. For report 0x1B, it gets 8 bytes back with buffer=8 and stops. Try reading 0x1B with buffer=260 directly:

```python
buf = bytearray(260)
buf[0] = 0x1B
fcntl.ioctl(fd, HIDIOCGFEATURE(260), buf)
print(f"Got: {buf.hex()}")

```

If this returns 260 bytes of data, the NCP channel may already be working and we just weren't reading the full response.
7.2 Parse the full report descriptor

```bash
# Get the raw descriptor
sudo python3 -c "
import os, fcntl, array, struct
fd = os.open('/dev/hidraw1', os.O_RDWR)
buf = array.array('i', [0])
fcntl.ioctl(fd, 0x80044801, buf)  # HIDIOCGRDESCSIZE
sz = buf[0]
desc = array.array('B', [0]*4100)
struct.pack_into('<I', desc, 0, sz)
fcntl.ioctl(fd, 0x90044802, desc)  # HIDIOCGRDESC
print(bytes(desc[4:4+sz]).hex())
os.close(fd)
"

```

Then paste into https://eleccelerator.com/usbdescreqparser/ or use `hidrd-convert` to understand which usage pages and report sizes are defined. This will tell us the exact expected size for report 0x1B and what usage page it's on.
7.3 Fix raw I2C device discovery
The `find_i2c_device()` function needs to handle `i2c-NTRG0001:01` format. It should:
1. Find the device at `/sys/bus/i2c/devices/i2c-NTRG0001:01`
2. Read its bus number from the parent symlink or from the device path
3. Read the slave address from `address` file or from I2C-HID descriptor

```bash
# Get the bus and address
cat /sys/bus/i2c/devices/i2c-NTRG0001:01/name
# Look at the parent bus
readlink -f /sys/bus/i2c/devices/i2c-NTRG0001:01/..

```

Actually, the naming `i2c-NTRG0001:01` is the ACPI companion device name, not a `bus-address` format. The actual I2C client address might be found differently. Check:

```bash
# This might show the address
cat /sys/bus/i2c/devices/i2c-NTRG0001:01/uevent
# Or look for:
dmesg | grep -i "NTRG\|ntrig\|1B96"

```

7.4 Try raw I2C with correct device info
Once you know the bus number and slave address:
1. `sudo modprobe i2c-dev`
2. Unbind: `echo "i2c-NTRG0001:01" | sudo tee /sys/bus/i2c/drivers/i2c_hid_acpi/unbind`
3. Open `/dev/i2c-N` (where N is the bus number)
4. Read HID descriptor from register 0x0001 to confirm cmd_reg and data_reg
5. Send GET_REPORT for 0x1B via raw I2C-HID protocol (bypasses all kernel validation)
6. Send SET_REPORT with NCP frame
7. Rebind: `echo "i2c-NTRG0001:01" | sudo tee /sys/bus/i2c/drivers/i2c_hid_acpi/bind`
The raw I2C bypasses the `i2c-hid-core.c` report ID validation, so even if the device responds with a different report ID, you'll see the raw bytes.
7.5 Deeper DLL reverse engineering
Use Ghidra to analyze how `NCPTransportInterface.dll` handles device initialization on I2C-HID:
* The DLL calls `HidP_GetCaps` which returns `FeatureReportByteLength` — this determines the report buffer size
* Look for how it maps NCP frames when the device has report 0x1B (259 bytes) instead of 0x2E (17 bytes)
* The NCP frame may need to be embedded at a specific offset within the 259-byte report, not necessarily at byte 1
* There may be an initialization sequence (writing to report 0x1B with specific magic bytes before NCP becomes active)
7.6 Windows I2C trace as definitive answer
If all else fails, the most reliable approach is:
1. Boot Windows (Windows-To-Go USB)
2. Enable HID/I2C tracing
3. Run CalibG4.exe
4. Capture the exact I2C-HID commands sent
5. Replicate from Linux
8. FILES AVAILABLE
All extracted files should be preserved:
* `EP0000601624.exe` — the original Sony wrapper
* `CalibG4.exe` (19KB) — the actual calibration tool (extracted)
* `NCPTransportInterface.dll` (151KB) — the HID communication DLL (extracted)
* `ntrig_calib.py` — the Python script (latest version with user's fixes applied)
* The user's patched version fixes `find_i2c_device()` parsing for non-numeric bus entries
9. THINGS I'M UNCERTAIN ABOUT
1. Whether report 0x1B is truly the NCP channel on I2C-HID — it's the strongest candidate (259 bytes, vendor-specific, responds to both GET and SET), but I haven't confirmed this with a Windows trace. The 8-byte truncated response `29 a9 19 9f 9a 19 a4` doesn't contain 0x7E (NCP marker), which could mean: (a) the NCP channel isn't initialized yet, (b) the data is at a different offset in the full 259-byte response, or (c) report 0x1B isn't the NCP channel at all.
2. Whether the NCP frame format is identical on I2C-HID — the frame might be wrapped differently on I2C-HID vs USB-HID. The DLL's code paths diverge based on transport type.
3. The exact I2C bus number and slave address for the SP3 N-Trig — the sysfs entry is `i2c-NTRG0001:01` which doesn't directly encode bus/address.
4. Whether the 260-byte read attempt actually reached the device or was kernel-rejected — the probe showed 0x1B responding at size 8, but we never tried 260 alone. The kernel might accept small reads but silently truncate.
5. Whether there's an initialization handshake — CalibG4 calls `Init()` then `Register()` before `SendRequest()`. The DLL's `Init()` does device enumeration, and `Register()` allocates a handle with a module ID derived from `UuidCreate()`. There may be a handshake involving reports 0x03, 0x04, or other reports before 0x1B becomes the NCP channel.
10. KERNEL AND DRIVER NOTES
* The `hid-ntrig.c` kernel driver is USB-only (includes `<linux/usb.h>`, matches `HID_USB_DEVICE`). It cannot bind to I2C-HID devices. It's irrelevant.
* The `hid-multitouch` driver is what actually handles the SP3 touchscreen on modern kernels. It creates a single `/dev/hidraw1` for the entire device (all HID collections merged).
* On Windows, each HID top-level collection gets its own device node (`Col01`, `Col02`, etc.). On Linux I2C-HID, there's only one hidraw node.
* The `i2c_hid_acpi` driver handles the I2C-HID transport and has the report ID validation that might interfere with NCP communication.
* HID-BPF (kernel 6.11+) could potentially intercept and fix report ID mismatches without unbinding drivers.

---


Now that you know all of that, know that the now most recent version of the code I made with that claude is:


```python
#!/usr/bin/env python3
"""
ntrig_calib.py v4 — N-Trig touchscreen recalibration for Surface Pro 3 (Linux)

Key lessons from v1-v3:
- Report 0x1B (259 bytes) returns static crypto/auth data — NOT the NCP channel
- SET_FEATURE on 0x1B "succeeds" via hidraw but device ignores NCP frames
- Raw I2C at 0x07 fails with EREMOTEIO because i2c_hid_acpi powers device down on unbind
- This version: wakes device after unbind, tries ALL reports for NCP, tries raw commands
"""

import os, sys, glob, struct, fcntl, time, array, argparse, re, ctypes

RED='\033[91m'; GREEN='\033[92m'; YELLOW='\033[93m'; CYAN='\033[96m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

def ok(msg):    print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg):  print(f"  {RED}✗{RESET} {msg}")
def warn(msg):  print(f"  {YELLOW}⚠{RESET} {msg}")
def info(msg):  print(f"  {CYAN}ℹ{RESET} {msg}")
def step(msg):  print(f"\n{BOLD}{'─'*60}\n  {msg}\n{'─'*60}{RESET}")
def hexdump(data, prefix="    ", maxlines=5):
    for i in range(0, min(maxlines*16, len(data)), 16):
        c = data[i:i+16]
        h = ' '.join(f'{b:02x}' for b in c)
        a = ''.join(chr(b) if 32<=b<127 else '.' for b in c)
        print(f"{prefix}{i:04x}: {h:<48s} {a}")

# ─── HID ioctl ────────────────────────────────────────────────────────────────
def HIDIOCSFEATURE(sz): return 0xC0004806 | (sz << 16)
def HIDIOCGFEATURE(sz): return 0xC0004807 | (sz << 16)
HIDIOCGRAWINFO   = 0x80084803
HIDIOCGRDESCSIZE = 0x80044801
HIDIOCGRDESC     = 0x90044802

# ─── NCP protocol ─────────────────────────────────────────────────────────────
NCP_MARKER = 0x7E
NTRIG_VID = 0x1B96

def ncp_checksum(data):
    return (-sum((b if b < 128 else b - 256) for b in data)) & 0xFF

def build_ncp_frame(cmd_group, cmd_id, module_id=0x0001, payload=b''):
    fsize = 14 + len(payload) + 1
    hdr = bytearray(14)
    hdr[0] = NCP_MARKER
    struct.pack_into('<H', hdr, 1, module_id)
    struct.pack_into('<H', hdr, 3, fsize)
    hdr[5] = 0x01; hdr[6] = cmd_group; hdr[7] = cmd_id
    body = bytes(hdr) + payload
    return body + bytes([ncp_checksum(body)])

# ─── hidraw helpers ───────────────────────────────────────────────────────────
def find_ntrig_hidraw():
    results = []
    for path in sorted(glob.glob('/dev/hidraw*')):
        try:
            fd = os.open(path, os.O_RDWR)
            try:
                buf = array.array('B', [0]*8)
                fcntl.ioctl(fd, HIDIOCGRAWINFO, buf)
                bt  = struct.unpack_from('<I', buf, 0)[0]
                vid = struct.unpack_from('<H', buf, 4)[0]
                pid = struct.unpack_from('<H', buf, 6)[0]
                if vid == NTRIG_VID:
                    results.append((path, vid, pid, bt))
            finally:
                os.close(fd)
        except (OSError, PermissionError):
            pass
    return results

def try_get(fd, rid, sz):
    buf = bytearray(sz); buf[0] = rid
    try:
        fcntl.ioctl(fd, HIDIOCGFEATURE(sz), buf)
        return True, bytes(buf)
    except OSError as e:
        return False, str(e)

def try_set(fd, data):
    buf = bytearray(data)
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)
        return True, None
    except OSError as e:
        return False, str(e)

# ─── Diagnostics (hidraw) — try ALL reports for NCP ──────────────────────────

def run_diagnostics(device_path):
    step("DIAGNOSTICS v4")
    fd = os.open(device_path, os.O_RDWR)
    try:
        buf = array.array('B', [0]*8)
        fcntl.ioctl(fd, HIDIOCGRAWINFO, buf)
        bt  = struct.unpack_from('<I', buf, 0)[0]
        vid = struct.unpack_from('<H', buf, 4)[0]
        pid = struct.unpack_from('<H', buf, 6)[0]
        info(f"Bus: {bt} (0x18=I2C)  VID: 0x{vid:04X}  PID: 0x{pid:04X}")

        # Get report descriptor
        step("REPORT DESCRIPTOR")
        dbuf = array.array('i', [0])
        fcntl.ioctl(fd, HIDIOCGRDESCSIZE, dbuf)
        dsz = dbuf[0]
        ddesc = array.array('B', [0]*4100)
        struct.pack_into('<I', ddesc, 0, dsz)
        fcntl.ioctl(fd, HIDIOCGRDESC, ddesc)
        rdesc = bytes(ddesc[4:4+dsz])
        report_ids = sorted(set(rdesc[i+1] for i in range(len(rdesc)-1) if rdesc[i]==0x85))
        info(f"{dsz} bytes, {len(report_ids)} report IDs: "
             f"{', '.join(f'0x{r:02X}' for r in report_ids)}")

        # Probe ALL report IDs at multiple sizes
        step("GET_FEATURE PROBE (all reports, large buffers first for known vendor reports)")
        working = {}
        for rid in report_ids:
            # Try large sizes first for vendor reports, small first for standard
            if rid >= 0x10:
                sizes = [260, 514, 65, 34, 16, 8]
            else:
                sizes = [8, 16, 34, 65, 260]
            for sz in sizes:
                ok_r, data = try_get(fd, rid, sz)
                if ok_r:
                    nz = sum(1 for b in data[1:] if b != 0)
                    tag = f"{GREEN}data({nz}nz){RESET}" if nz else f"{YELLOW}zeros{RESET}"
                    print(f"  0x{rid:02X}  sz={sz:3d}  {tag}  {data[:16].hex()}")
                    working[rid] = (sz, data)
                    break
            else:
                print(f"  0x{rid:02X}  {DIM}no response{RESET}")

        # Now try SET_FEATURE on EVERY report ID, not just working ones
        step("SET_FEATURE PROBE (try writing NCP to every report)")
        info("Goal: find which report accepts NCP commands\n")

        ncp_get_status = build_ncp_frame(0x20, 0x0B, module_id=0x0001)
        ncp_start_cal  = build_ncp_frame(0x20, 0x0A, module_id=0x0001)

        # Snapshot all working reports before sends
        pre_snapshot = {}
        for rid, (sz, _) in working.items():
            ok_r, data = try_get(fd, rid, sz)
            if ok_r:
                pre_snapshot[rid] = data

        set_results = {}
        for rid in report_ids:
            # Determine buffer size: use working size if known, else try common sizes
            if rid in working:
                sz = working[rid][0]
            elif rid >= 0x10:
                sz = 260
            else:
                sz = 8

            # Build report: [report_id, ncp_frame, zero_padding...]
            report = bytearray(sz)
            report[0] = rid
            frame_len = min(len(ncp_get_status), sz - 1)
            report[1:1+frame_len] = ncp_get_status[:frame_len]

            ok_s, err = try_set(fd, report)
            status = f"{GREEN}OK{RESET}" if ok_s else f"{RED}{err}{RESET}"
            print(f"  SET 0x{rid:02X} (sz={sz:3d}): {status}")
            set_results[rid] = ok_s

            if ok_s:
                # Check if ANY working report changed
                time.sleep(0.05)
                for chk_rid, (chk_sz, _) in working.items():
                    ok_r, new_data = try_get(fd, chk_rid, chk_sz)
                    if ok_r and chk_rid in pre_snapshot:
                        if new_data != pre_snapshot[chk_rid]:
                            ok(f"  → Report 0x{chk_rid:02X} CHANGED after writing to 0x{rid:02X}!")
                            info(f"    Before: {pre_snapshot[chk_rid][:16].hex()}")
                            info(f"    After:  {new_data[:16].hex()}")
                            # Check for NCP marker
                            for off in range(len(new_data)):
                                if new_data[off] == NCP_MARKER:
                                    ok(f"    NCP marker (0x7E) at offset {off}!")
                                    hexdump(new_data[off:off+32], prefix="      ")
                                    break
                            # Update snapshot
                            pre_snapshot[chk_rid] = new_data

        # Try writing raw calibration bytes WITHOUT NCP framing
        step("RAW COMMAND TEST (no NCP framing)")
        info("Maybe I2C-HID N-Trig uses raw commands, not NCP frames\n")

        # The old hid-ntrig.c USB driver used simple feature report writes
        # for mode switching. Try similar patterns.
        raw_commands = [
            ("Mode switch (0x0A=calibrate?)", 0x0A, bytes([0x0A, 0x01])),
            ("Mode switch (0x0D=normal)", 0x0D, bytes([0x0D, 0x00])),
            ("Calibrate cmd (group+id)", 0x0A, bytes([0x0A, 0x20, 0x0A])),
            ("0x1B with 0x7E header", 0x1B, ncp_get_status + bytes(258 - len(ncp_get_status))),
        ]
        for desc, rid, payload in raw_commands:
            report = bytearray(max(len(payload)+1, working.get(rid, (8,))[0]))
            report[0] = rid
            report[1:1+len(payload)] = payload
            ok_s, err = try_set(fd, report)
            result = f"{GREEN}OK{RESET}" if ok_s else f"{DIM}{err}{RESET}"
            print(f"  {desc}: SET 0x{rid:02X} → {result}")
            if ok_s:
                time.sleep(0.05)
                for chk_rid in [0x03, 0x04, 0x1B]:
                    if chk_rid in working:
                        ok_r, d = try_get(fd, chk_rid, working[chk_rid][0])
                        if ok_r and chk_rid in pre_snapshot and d != pre_snapshot[chk_rid]:
                            ok(f"    → 0x{chk_rid:02X} changed! {d[:16].hex()}")
                            pre_snapshot[chk_rid] = d

        step("SUMMARY")
        set_ok = [f"0x{r:02X}" for r, v in set_results.items() if v]
        set_fail = [f"0x{r:02X}" for r, v in set_results.items() if not v]
        info(f"SET_FEATURE accepted by: {', '.join(set_ok) if set_ok else 'none'}")
        info(f"SET_FEATURE rejected by: {', '.join(set_fail) if set_fail else 'none'}")
        if all(not v for v in set_results.values()):
            fail("No report accepted SET_FEATURE — try --raw-i2c --force")
        elif set_ok:
            info("Reports that accepted writes but didn't change state:")
            info("→ Device may need ACPI/power init sequence first")
            info("→ Try: sudo python3 ntrig.py --raw-i2c --force")

    finally:
        os.close(fd)


# ─── Raw I2C ──────────────────────────────────────────────────────────────────

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001

class i2c_msg(ctypes.Structure):
    _fields_ = [('addr', ctypes.c_ushort), ('flags', ctypes.c_ushort),
                 ('len', ctypes.c_ushort), ('buf', ctypes.POINTER(ctypes.c_ubyte))]

class i2c_rdwr_ioctl_data(ctypes.Structure):
    _fields_ = [('msgs', ctypes.POINTER(i2c_msg)), ('nmsgs', ctypes.c_uint)]

def i2c_write_read(fd, addr, wdata, rlen):
    wbuf = (ctypes.c_ubyte * len(wdata))(*wdata)
    rbuf = (ctypes.c_ubyte * rlen)()
    msgs = (i2c_msg * 2)(
        i2c_msg(addr=addr, flags=0, len=len(wdata),
                buf=ctypes.cast(wbuf, ctypes.POINTER(ctypes.c_ubyte))),
        i2c_msg(addr=addr, flags=I2C_M_RD, len=rlen,
                buf=ctypes.cast(rbuf, ctypes.POINTER(ctypes.c_ubyte))))
    rdwr = i2c_rdwr_ioctl_data(
        msgs=ctypes.cast(msgs, ctypes.POINTER(i2c_msg)), nmsgs=2)
    fcntl.ioctl(fd, I2C_RDWR, rdwr)
    return bytes(rbuf)

def i2c_write(fd, addr, wdata):
    wbuf = (ctypes.c_ubyte * len(wdata))(*wdata)
    msgs = (i2c_msg * 1)(
        i2c_msg(addr=addr, flags=0, len=len(wdata),
                buf=ctypes.cast(wbuf, ctypes.POINTER(ctypes.c_ubyte))))
    rdwr = i2c_rdwr_ioctl_data(
        msgs=ctypes.cast(msgs, ctypes.POINTER(i2c_msg)), nmsgs=1)
    fcntl.ioctl(fd, I2C_RDWR, rdwr)

def i2c_read(fd, addr, rlen):
    rbuf = (ctypes.c_ubyte * rlen)()
    msgs = (i2c_msg * 1)(
        i2c_msg(addr=addr, flags=I2C_M_RD, len=rlen,
                buf=ctypes.cast(rbuf, ctypes.POINTER(ctypes.c_ubyte))))
    rdwr = i2c_rdwr_ioctl_data(
        msgs=ctypes.cast(msgs, ctypes.POINTER(i2c_msg)), nmsgs=1)
    fcntl.ioctl(fd, I2C_RDWR, rdwr)
    return bytes(rbuf)


def find_i2c_info():
    """Find N-Trig I2C bus number, slave address, device name, driver name."""
    for dev_path in glob.glob('/sys/bus/i2c/devices/*'):
        basename = os.path.basename(dev_path)
        is_ntrig = False
        for f in ['name', 'uevent']:
            fp = os.path.join(dev_path, f)
            if os.path.exists(fp):
                try:
                    with open(fp) as fh:
                        if 'NTRG' in fh.read():
                            is_ntrig = True; break
                except OSError:
                    pass
        if not is_ntrig:
            continue

        info(f"sysfs: {basename}")
        real = os.path.realpath(dev_path)
        info(f"  resolved: {real}")

        # Get bus number from path
        m = re.search(r'/i2c-(\d+)/', real)
        bus = int(m.group(1)) if m else None

        # Get address: look for numeric child like "1-0007"
        addr = None
        for child in glob.glob(os.path.join(real, '*')):
            cb = os.path.basename(child)
            m2 = re.match(r'^(\d+)-([0-9a-fA-F]{4})$', cb)
            if m2:
                addr = int(m2.group(2), 16)
                info(f"  child client: {cb} → addr=0x{addr:02X}")
                break

        # Fallback: check /sys/bus/i2c/devices/N-XXXX directly
        if addr is None:
            for other in glob.glob('/sys/bus/i2c/devices/*'):
                ob = os.path.basename(other)
                m3 = re.match(r'^(\d+)-([0-9a-fA-F]{4})$', ob)
                if m3:
                    oreal = os.path.realpath(other)
                    if 'NTRG' in oreal or 'ntrg' in oreal.lower():
                        addr = int(m3.group(2), 16)
                        bus = int(m3.group(1))
                        info(f"  found client: {ob} → bus={bus} addr=0x{addr:02X}")
                        break

        if addr is None:
            addr = 0x07
            warn(f"  guessing addr=0x{addr:02X} (ACPI SSDT2 value for SP3)")

        # Get bound driver
        driver = None
        drv_link = os.path.join(dev_path, 'driver')
        if os.path.islink(drv_link):
            driver = os.path.basename(os.readlink(drv_link))

        return bus, addr, dev_path, basename, driver

    return None, None, None, None, None


def run_raw_i2c(args):
    step("RAW I2C MODE v4")

    bus, addr, syspath, devname, driver = find_i2c_info()
    if bus is None:
        fail("N-Trig I2C device not found")
        info("Run: ls -la /sys/bus/i2c/devices/")
        return False

    ok(f"Bus {bus}, addr 0x{addr:02X}, driver={driver}")
    i2c_dev = f"/dev/i2c-{bus}"

    if not os.path.exists(i2c_dev):
        os.system("modprobe i2c-dev"); time.sleep(0.5)
    if not os.path.exists(i2c_dev):
        fail(f"{i2c_dev} missing"); return False

    # Unbind driver
    if driver and not args.force:
        warn(f"Driver '{driver}' bound — run with --force to unbind")
        return False

    if driver:
        info(f"Unbinding {devname} from {driver}...")
        try:
            with open(f"/sys/bus/i2c/drivers/{driver}/unbind", 'w') as f:
                f.write(devname)
            ok("Unbound")
            time.sleep(0.5)
        except OSError as e:
            fail(f"Unbind failed: {e}"); return False

    fd = os.open(i2c_dev, os.O_RDWR)
    try:
        # ── Step 1: Wake the device ──
        # i2c_hid's remove() sends SET_POWER(SLEEP). We must re-power it.
        step("WAKING DEVICE")
        info("i2c_hid powers device down on unbind — sending SET_POWER(ON)...")

        # I2C-HID spec: SET_POWER writes [cmd_reg_lo, cmd_reg_hi, powerState, 0x08]
        # powerState: 0x00=ON, 0x01=SLEEP. Opcode 0x08.
        # Default cmd_reg = 0x0005
        cmd_reg = 0x0005
        data_reg = 0x0006

        # Try SET_POWER(ON) — this is a write-only command
        set_power_on = bytes([cmd_reg & 0xFF, cmd_reg >> 8, 0x00, 0x08])
        try:
            i2c_write(fd, addr, set_power_on)
            ok("SET_POWER(ON) sent")
            time.sleep(0.1)
        except OSError as e:
            warn(f"SET_POWER(ON) failed: {e}")
            info("Trying RESET command instead...")
            # I2C-HID RESET: [cmd_reg_lo, cmd_reg_hi, 0x00, 0x01]
            reset_cmd = bytes([cmd_reg & 0xFF, cmd_reg >> 8, 0x00, 0x01])
            try:
                i2c_write(fd, addr, reset_cmd)
                ok("RESET sent")
                time.sleep(1)  # Device needs time to reset
                # After reset, need to read the mandatory 2-byte length from input register
                try:
                    resp = i2c_read(fd, addr, 2)
                    info(f"Post-reset input: {resp.hex()}")
                except OSError:
                    pass
            except OSError as e2:
                fail(f"RESET also failed: {e2}")
                fail("Device not responding at address 0x{addr:02X} on bus {bus}")
                info("The address might be wrong. Scanning nearby addresses...")
                for test_addr in range(0x01, 0x78):
                    try:
                        i2c_read(fd, test_addr, 1)
                        ok(f"  Device found at 0x{test_addr:02X}!")
                    except OSError:
                        pass
                return False

        # ── Step 2: Read HID descriptor ──
        step("I2C-HID DESCRIPTOR")
        try:
            desc = i2c_write_read(fd, addr, bytes([0x01, 0x00]), 30)
            info(f"Raw: {desc.hex()}")
            if len(desc) >= 22:
                info(f"  Length:     {struct.unpack_from('<H', desc, 0)[0]}")
                info(f"  BCD ver:   0x{struct.unpack_from('<H', desc, 2)[0]:04X}")
                rd_len = struct.unpack_from('<H', desc, 4)[0]
                info(f"  ReportDesc:{rd_len}b at reg 0x{struct.unpack_from('<H', desc, 6)[0]:04X}")
                info(f"  Input:     0x{struct.unpack_from('<H', desc, 8)[0]:04X} "
                     f"(max {struct.unpack_from('<H', desc, 10)[0]}b)")
                cmd_reg  = struct.unpack_from('<H', desc, 16)[0]
                data_reg = struct.unpack_from('<H', desc, 18)[0]
                v = struct.unpack_from('<H', desc, 20)[0]
                p = struct.unpack_from('<H', desc, 22)[0]
                info(f"  CmdReg:    0x{cmd_reg:04X}")
                info(f"  DataReg:   0x{data_reg:04X}")
                info(f"  VID:       0x{v:04X}  PID: 0x{p:04X}")
        except OSError as e:
            warn(f"HID desc read failed: {e} — using defaults (cmd=0x0005, data=0x0006)")

        # ── Step 3: GET_REPORT probe ──
        step("RAW I2C GET_REPORT")
        def get_report(rid, maxr=263):
            if rid <= 0x0F:
                cmd = bytes([cmd_reg&0xFF, cmd_reg>>8, 0x30|(rid&0x0F), 0x02,
                             data_reg&0xFF, data_reg>>8])
            else:
                cmd = bytes([cmd_reg&0xFF, cmd_reg>>8, 0x3F, 0x02, rid,
                             data_reg&0xFF, data_reg>>8])
            resp = i2c_write_read(fd, addr, cmd, maxr)
            rlen = struct.unpack_from('<H', resp, 0)[0]
            return resp[2:rlen] if rlen > 2 else None

        def set_report(rid, payload):
            total = 3 + len(payload)  # len(2) + rid(1) + data
            if rid <= 0x0F:
                cmd = bytearray([cmd_reg&0xFF, cmd_reg>>8, 0x30|(rid&0x0F), 0x03,
                                 data_reg&0xFF, data_reg>>8,
                                 total&0xFF, total>>8, rid]) + bytearray(payload)
            else:
                cmd = bytearray([cmd_reg&0xFF, cmd_reg>>8, 0x3F, 0x03, rid,
                                 data_reg&0xFF, data_reg>>8,
                                 total&0xFF, total>>8, rid]) + bytearray(payload)
            i2c_write(fd, addr, bytes(cmd))

        raw_working = {}
        for rid in [0x03, 0x04, 0x05, 0x07, 0x0A, 0x0B, 0x0C, 0x11, 0x15, 0x18, 0x1B, 0x2E]:
            try:
                resp = get_report(rid)
                if resp and len(resp) > 0:
                    nz = sum(1 for b in resp if b != 0)
                    ok(f"0x{rid:02X}: {len(resp)}b ({nz} non-zero)")
                    if len(resp) <= 32:
                        info(f"  {resp.hex()}")
                    else:
                        hexdump(resp, maxlines=3)
                    raw_working[rid] = resp
                    # Check for NCP marker
                    if NCP_MARKER in resp:
                        p = list(resp).index(NCP_MARKER)
                        ok(f"  0x7E at offset {p}!")
                else:
                    print(f"  0x{rid:02X}: {DIM}empty{RESET}")
            except OSError as e:
                print(f"  0x{rid:02X}: {DIM}{e}{RESET}")

        # ── Step 4: Try NCP via SET_REPORT on each working report ──
        step("RAW I2C NCP SEND")
        ncp_frame = build_ncp_frame(0x20, 0x0B)  # GET_STATUS

        for rid in sorted(raw_working.keys()):
            resp_len = len(raw_working[rid])
            # Pad NCP frame to match report size
            padded = ncp_frame + bytes(max(0, resp_len - len(ncp_frame)))
            info(f"SET_REPORT 0x{rid:02X} with NCP GET_STATUS ({len(padded)}b)...")
            try:
                set_report(rid, padded)
                ok("Sent")
                time.sleep(0.1)
                # Read back same report
                try:
                    resp = get_report(rid)
                    if resp:
                        changed = resp != raw_working[rid]
                        tag = f"{GREEN}CHANGED{RESET}" if changed else "unchanged"
                        info(f"  Response ({len(resp)}b, {tag}): {resp[:24].hex()}")
                        if NCP_MARKER in resp:
                            p = list(resp).index(NCP_MARKER)
                            ok(f"  NCP frame at offset {p}!")
                            hexdump(resp[p:p+32])
                except OSError as e:
                    warn(f"  Read back failed: {e}")
            except OSError as e:
                fail(f"  SET failed: {e}")

        # ── Step 5: Try NCP START_CALIB if anything looked promising ──
        step("RAW I2C START CALIBRATION ATTEMPT")
        ncp_start = build_ncp_frame(0x20, 0x0A)
        for rid in sorted(raw_working.keys()):
            resp_len = len(raw_working[rid])
            padded = ncp_start + bytes(max(0, resp_len - len(ncp_start)))
            info(f"SET_REPORT 0x{rid:02X} with NCP START_CALIB...")
            try:
                set_report(rid, padded)
                ok("Sent")
            except OSError as e:
                fail(f"  {e}")
                continue

            # Poll for changes
            info("  Polling 5 times...")
            for i in range(5):
                time.sleep(0.5)
                try:
                    resp = get_report(rid)
                    if resp:
                        changed = resp != raw_working[rid]
                        if changed:
                            ok(f"  Poll {i+1}: CHANGED! {resp[:24].hex()}")
                            if NCP_MARKER in resp:
                                p = list(resp).index(NCP_MARKER)
                                ok(f"  NCP at offset {p}!")
                                hexdump(resp[p:p+32])
                        else:
                            print(f"    Poll {i+1}: same")
                except OSError:
                    print(f"    Poll {i+1}: read error")

    except Exception as e:
        fail(f"Unexpected error: {e}")
        import traceback; traceback.print_exc()

    finally:
        os.close(fd)
        if driver:
            step("REBINDING DRIVER")
            try:
                with open(f"/sys/bus/i2c/drivers/{driver}/bind", 'w') as f:
                    f.write(devname)
                ok(f"Rebound to {driver}")
            except OSError as e:
                warn(f"Rebind failed: {e}")
                warn(f"  echo '{devname}' | sudo tee /sys/bus/i2c/drivers/{driver}/bind")

    return True


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='N-Trig calibration for SP3 (Linux)')
    p.add_argument('-d', '--device', help='hidraw device')
    p.add_argument('--diag', action='store_true', help='diagnostics')
    p.add_argument('--raw-i2c', action='store_true', help='bypass HID stack')
    p.add_argument('--force', action='store_true', help='auto-unbind driver')
    p.add_argument('--module-id', type=lambda x: int(x, 0), default=0x0001)
    args = p.parse_args()

    if os.geteuid() != 0:
        print(f"{RED}Requires root.{RESET}"); sys.exit(1)

    if args.raw_i2c:
        sys.exit(0 if run_raw_i2c(args) else 1)

    dev = args.device
    if not dev:
        devices = find_ntrig_hidraw()
        if not devices:
            fail("No N-Trig hidraw devices"); sys.exit(1)
        for p2, v, pid, bt in devices:
            info(f"{p2}: 0x{v:04X}:0x{pid:04X} bus={'I2C' if bt==0x18 else 'USB' if bt==3 else bt}")
        dev = devices[0][0]
        ok(f"Using {dev}")

    # Default to diag mode — calibration only makes sense once we find the NCP channel
    if not args.diag:
        warn("Running --diag by default (calibration needs working NCP channel first)")
    run_diagnostics(dev)

if __name__ == '__main__':
    main()

```

And here is the result of the command running:

```
sudo python3 ntrig.py --diag
[sudo] password for surf: 
  ℹ /dev/hidraw1: 0x1B96:0x1B05 bus=I2C
  ✓ Using /dev/hidraw1

────────────────────────────────────────────────────────────
  DIAGNOSTICS v4
────────────────────────────────────────────────────────────
  ℹ Bus: 24 (0x18=I2C)  VID: 0x1B96  PID: 0x1B05

────────────────────────────────────────────────────────────
  REPORT DESCRIPTOR
────────────────────────────────────────────────────────────
  ℹ 455 bytes, 16 report IDs: 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0C, 0x11, 0x15, 0x18, 0x1B, 0x58

────────────────────────────────────────────────────────────
  GET_FEATURE PROBE (all reports, large buffers first for known vendor reports)
────────────────────────────────────────────────────────────
  0x01  no response
  0x02  no response
  0x03  sz=  8  data(6nz)  035714e701009c1d
  0x04  sz=  8  data(1nz)  040a000000000000
  0x05  no response
  0x06  no response
  0x07  no response
  0x08  no response
  0x0A  no response
  0x0B  no response
  0x0C  no response
  0x11  no response
  0x15  no response
  0x18  no response
  0x1B  sz=260  data(256nz)  1b29a9199f9a19a4bfddc2f329e13444
  0x58  no response

────────────────────────────────────────────────────────────
  SET_FEATURE PROBE (try writing NCP to every report)
────────────────────────────────────────────────────────────
  ℹ Goal: find which report accepts NCP commands

  SET 0x01 (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x01!
  ℹ     Before: 036014e701009c1d
  ℹ     After:  036514e701009c1d
  SET 0x02 (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x02!
  ℹ     Before: 036514e701009c1d
  ℹ     After:  036a14e701009c1d
  SET 0x03 (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x03!
  ℹ     Before: 036a14e701009c1d
  ℹ     After:  037014e701009c1d
  SET 0x04 (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x04!
  ℹ     Before: 037014e701009c1d
  ℹ     After:  037514e701009c1d
  SET 0x05 (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x05!
  ℹ     Before: 037514e701009c1d
  ℹ     After:  037b14e701009c1d
  SET 0x06 (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x06!
  ℹ     Before: 037b14e701009c1d
  ℹ     After:  038014e701009c1d
  SET 0x07 (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x07!
  ℹ     Before: 038014e701009c1d
  ℹ     After:  038614e701009c1d
  SET 0x08 (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x08!
  ℹ     Before: 038614e701009c1d
  ℹ     After:  038b14e701009c1d
  SET 0x0A (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x0A!
  ℹ     Before: 038b14e701009c1d
  ℹ     After:  039114e701009c1d
  SET 0x0B (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x0B!
  ℹ     Before: 039114e701009c1d
  ℹ     After:  039614e701009c1d
  SET 0x0C (sz=  8): OK
  ✓   → Report 0x03 CHANGED after writing to 0x0C!
  ℹ     Before: 039614e701009c1d
  ℹ     After:  039c14e701009c1d
  SET 0x11 (sz=260): OK
  ✓   → Report 0x03 CHANGED after writing to 0x11!
  ℹ     Before: 039c14e701009c1d
  ℹ     After:  03a114e701009c1d
  SET 0x15 (sz=260): OK
  ✓   → Report 0x03 CHANGED after writing to 0x15!
  ℹ     Before: 03a114e701009c1d
  ℹ     After:  03a714e701009c1d
  SET 0x18 (sz=260): OK
  ✓   → Report 0x03 CHANGED after writing to 0x18!
  ℹ     Before: 03a714e701009c1d
  ℹ     After:  03ad14e701009c1d
  SET 0x1B (sz=260): OK
  ✓   → Report 0x03 CHANGED after writing to 0x1B!
  ℹ     Before: 03ad14e701009c1d
  ℹ     After:  03b214e701009c1d
  SET 0x58 (sz=260): OK
  ✓   → Report 0x03 CHANGED after writing to 0x58!
  ℹ     Before: 03b214e701009c1d
  ℹ     After:  03b814e701009c1d

────────────────────────────────────────────────────────────
  RAW COMMAND TEST (no NCP framing)
────────────────────────────────────────────────────────────
  ℹ Maybe I2C-HID N-Trig uses raw commands, not NCP frames

  Mode switch (0x0A=calibrate?): SET 0x0A → OK
  ✓     → 0x03 changed! 03be14e701009c1d
  Mode switch (0x0D=normal): SET 0x0D → OK
  ✓     → 0x03 changed! 03c314e701009c1d
  Calibrate cmd (group+id): SET 0x0A → OK
  ✓     → 0x03 changed! 03c814e701009c1d
  0x1B with 0x7E header: SET 0x1B → OK
  ✓     → 0x03 changed! 03ce14e701009c1d

────────────────────────────────────────────────────────────
  SUMMARY
────────────────────────────────────────────────────────────
  ℹ SET_FEATURE accepted by: 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0C, 0x11, 0x15, 0x18, 0x1B, 0x58
  ℹ SET_FEATURE rejected by: none
  ℹ Reports that accepted writes but didn't change state:
  ℹ → Device may need ACPI/power init sequence first
  ℹ → Try: sudo python3 ntrig.py --raw-i2c --force
surf@surf:~/Desktop$ sudo python3 ntrig.py --raw-i2c --force

────────────────────────────────────────────────────────────
  RAW I2C MODE v4
────────────────────────────────────────────────────────────
  ℹ sysfs: i2c-NTRG0001:01
  ℹ   resolved: /sys/devices/pci0000:00/INT33C3:00/i2c-1/i2c-NTRG0001:01
  ⚠   guessing addr=0x07 (ACPI SSDT2 value for SP3)
  ✓ Bus 1, addr 0x07, driver=i2c_hid_acpi
  ℹ Unbinding i2c-NTRG0001:01 from i2c_hid_acpi...
  ✓ Unbound

────────────────────────────────────────────────────────────
  WAKING DEVICE
────────────────────────────────────────────────────────────
  ℹ i2c_hid powers device down on unbind — sending SET_POWER(ON)...
  ⚠ SET_POWER(ON) failed: [Errno 121] Remote I/O error
  ℹ Trying RESET command instead...
  ✗ RESET also failed: [Errno 121] Remote I/O error
  ✗ Device not responding at address 0x{addr:02X} on bus {bus}
  ℹ The address might be wrong. Scanning nearby addresses...

────────────────────────────────────────────────────────────
  REBINDING DRIVER
────────────────────────────────────────────────────────────
  ✓ Rebound to i2c_hid_acpi
```

So please, unless you have a very specific solution in mind for the python script, I think the best course of action for you is to dig deeper inside the exe using ghidra and others to figure out in more details exactly what must be done to
</me>
<assistant>
Let me dig into the binaries directly. The diagnostic output tells us something critical: **Report 0x03 is just a transaction counter** (increments ~5-6 per write regardless of target), meaning none of our NCP frames got any real response. The device is likely ignoring them because we're missing an initialization/registration handshake.

Let me analyze the DLL to find that handshake.Good. Now let me trace the actual implementation functions — `0x18000c8f0` (Init impl), `0x18000c120` (Register impl), and `0x18000bd20` (the actual send path):Critical finding: `create_transport` at `0x1800010a0` has a branch — calls `0x180003110` to detect transport type, then constructs either a USB transport (`0x180001280`, 0xB0 bytes) or I2C transport (`0x180008730`, 0xB8 bytes). I need both constructors and the detector:Now I need the I2C transport's vtable to find its actual Init and Send implementations:Now let me trace the I2C send path from 0x180008d7f where the actual HidD_SetFeature call is, and critically, what report ID it selects:Now I have enough to write the definitive analysis. Let me compile all the key findings and write the updated script:Now I have the full picture. Let me write the complete findings and the updated script:

Here's what the deep DLL analysis revealed, structured by priority:

---

## What we found (definitive findings)

### Finding 1: Two completely different send paths exist

The I2C transport (`NCPTransportInterface.dll`'s class for bus-type I2C) has a **fork** in `i2c_send` (vtable[1], `0x1800088C0`) at address `0x180008D87`:

```
if [this+0x7c] != 0:
    → CHUNKED PATH via report 0x05  (function 0x18000cc80)
else:
    → DIRECT PATH via reports 0x29–0x2D  (function 0x1800011b0 selects ID)
```

**The direct path report IDs** (hardcoded in `0x1800011b0`):

| NCP frame size | Report ID | Report buffer |
|---|---|---|
| < 16 bytes | 0x29 | 16 bytes |
| < 32 bytes | 0x2A | 32 bytes |
| < 63 bytes | 0x2B | 63 bytes |
| < 255 bytes | 0x2C | 255 bytes |
| < 511 bytes | 0x2D | 511 bytes |

**None of these exist in the SP3's Linux HID descriptor.** We've been trying 0x1B (259 bytes) — that's the wrong report entirely.

### Finding 2: The chunked path uses report 0x05 — which DOES exist

Function `0x18000cc80` (the alternate path) sends data as:
```
Each HID write = 61 bytes: [0x05] [remaining_chunks] [59 bytes of NCP frame data]
```
Where `remaining_chunks` counts **down** — the last chunk has value `0`. For a 15-byte NCP frame (START_CALIB with no payload), this is a single 61-byte write: `[0x05][0x00][ncp_15_bytes][zeros...]`.

This path is activated when `[this+0x7c] = 1`, which gets set by `0x1800095d0` (the device probe/capability-check function) when probe with `cmd_id=0x0C` succeeds.

### Finding 3: The `[this+0x7c]` flag is set by a capability probe

`0x1800095d0` uses SetupDi to enumerate HID devices with VID=0x1B96, calls `HidP_GetCaps` on each one, and compares capabilities against three probe configurations (cmd_id = 0x01, 0x0B, 0x0C). On the SP3 running Windows, one of these presumably succeeds and routes the DLL into the chunked send path.

### Finding 4: Report 0x1B is definitely NOT the NCP channel

The data returned (`29 a9 19 9f 9a 19 a4 ...`) is static pseudo-random — consistent with a cryptographic device state register. Our SET writes to it are silently accepted by the kernel but ignored by the device.

### Finding 5: The receive path is async input reports, not GET_FEATURE polling

The DLL's receive thread reads from the HID device using `ReadFile` (async I/O), **not** `HidD_GetFeature`. On Linux this maps to `read()` on the hidraw fd. The script now does a non-blocking `select()` + `read()` after sends to catch this.

---

## What the v5 script does differently

1. **Parses the report descriptor properly** to get exact sizes for every report ID (not a hardcoded list)
2. **Sends NCP frames via report 0x05 in 61-byte chunks** with the counter byte
3. **Also probes reports 0x29–0x2D** even though they're not in the descriptor (some devices respond to undeclared reports)
4. **Reads async input reports** after sending (because the DLL uses `ReadFile`, not `GetFeature`)
5. **Lists all N-Trig hidraw devices** — the SP3 on Linux might expose multiple collections

---

## Biggest remaining uncertainty

I can't determine from static analysis **which hidraw device the DLL ultimately opens** for the `[this+0x30]` handle used in `HidD_SetFeature`. The probe function `0x1800095d0` opens devices via SetupDi enumeration and stores the handle during the `0x18000b110` call — but I don't have the full call chain for that function. On Windows with the SP3, it's possible it finds a **second** HID device (a separate NCP collection device node) that on Linux gets merged into `/dev/hidraw1` or exposed as a separate `/dev/hidraw0`.

**The single most actionable test you can do right now:**

```bash
ls /dev/hidraw*
sudo python3 ntrig_calib_v5.py --list
```

If there's a `/dev/hidraw0` as well, run the script against that one explicitly with `-d /dev/hidraw0`. The NCP collection device might be there.
<file>
#!/usr/bin/env python3
"""
ntrig_calib.py v5 — N-Trig touchscreen recalibration for Surface Pro 3 (Linux)

KEY FINDINGS FROM DEEP DLL REVERSE ENGINEERING (NCPTransportInterface.dll):

TWO TRANSPORT PATHS:
  1. MAIN PATH (function 0x1800088C0):
     - Uses report IDs 0x29–0x2D (hardcoded in 0x1800011b0)
     - Size table: <16→0x29(16b), <32→0x2A(32b), <63→0x2B(63b),
                   <255→0x2C(255b), <511→0x2D(511b)
     - These report IDs DON'T exist in the SP3's I2C-HID descriptor on Linux.
       On Windows, they might appear on a separate HID collection device node.

  2. ALTERNATE CHUNKED PATH (function 0x18000cc80, active when this→[0x7c]=1):
     - Uses report 0x05 (EXISTS in SP3 descriptor!)
     - Each HID write = 61 bytes: [0x05][remaining_chunks][59 bytes of NCP data]
     - remaining_chunks counts DOWN from N to 0; last chunk = 0
     - Report 0x3D = 61 bytes total per HidD_SetFeature call
     - Activated when device probe (0x1800095d0) succeeds with cmd_id=0x0C

  RESPONSE READING: The DLL's receive thread reads async HID Input reports.
  On Linux, we need to poll GET_FEATURE on all reports after sending.
  Candidate response reports: 0x0B, 0x0C (the ones probed for in 0x1800095d0).

NCP FRAME FORMAT (confirmed from DLL 0x18000d0d0):
  [0x7E][module_id LE16][frame_size LE16][flags][cmd_group][cmd_id]
  [seq_num LE32][0x00][0x00][payload...][checksum]
  checksum = (-sum(signed_bytes_0_to_N-2)) & 0xFF

WHAT WE KNOW IS WRONG:
  - Sending NCP to report 0x1B: SET_FEATURE succeeds but device ignores it.
    0x1B is probably crypto/auth state, not the NCP channel.
  - Report 0x03 changing on every write: it's just a HID transaction counter,
    not a meaningful NCP response.

WHAT TO TRY (this script):
  1. Parse HID descriptor to find report 0x05's exact size
  2. Send NCP frames via report 0x05 in 61-byte chunks
  3. After sending, poll ALL reports for changes / NCP markers
  4. Also try undeclared reports 0x29-0x2D in case they're hidden
  5. Enumerate ALL hidraw devices (not just hidraw1) - SP3 might have multiple
"""

import os, sys, glob, struct, fcntl, time, array, argparse, re, ctypes

RED='\033[91m'; GREEN='\033[92m'; YELLOW='\033[93m'; CYAN='\033[96m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

def ok(msg):    print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg):  print(f"  {RED}✗{RESET} {msg}")
def warn(msg):  print(f"  {YELLOW}⚠{RESET} {msg}")
def info(msg):  print(f"  {CYAN}ℹ{RESET} {msg}")
def step(msg):  print(f"\n{BOLD}{'─'*60}\n  {msg}\n{'─'*60}{RESET}")
def hexdump(data, prefix="    ", maxlines=5):
    for i in range(0, min(maxlines*16, len(data)), 16):
        c = data[i:i+16]
        h = ' '.join(f'{b:02x}' for b in c)
        a = ''.join(chr(b) if 32<=b<127 else '.' for b in c)
        print(f"{prefix}{i:04x}: {h:<48s} {a}")

# ─── HID ioctl ────────────────────────────────────────────────────────────────
def HIDIOCSFEATURE(sz): return 0xC0004806 | (sz << 16)
def HIDIOCGFEATURE(sz): return 0xC0004807 | (sz << 16)
HIDIOCGRAWINFO   = 0x80084803
HIDIOCGRDESCSIZE = 0x80044801
HIDIOCGRDESC     = 0x90044802

NTRIG_VID = 0x1B96

# ─── NCP protocol ─────────────────────────────────────────────────────────────
NCP_MARKER = 0x7E

def ncp_checksum(data):
    return (-sum((b if b < 128 else b - 256) for b in data)) & 0xFF

def build_ncp_frame(cmd_group, cmd_id, module_id=0x0001, payload=b''):
    fsize = 14 + len(payload) + 1
    hdr = bytearray(14)
    hdr[0] = NCP_MARKER
    struct.pack_into('<H', hdr, 1, module_id)
    struct.pack_into('<H', hdr, 3, fsize)
    hdr[5] = 0x01; hdr[6] = cmd_group; hdr[7] = cmd_id
    body = bytes(hdr) + payload
    return body + bytes([ncp_checksum(body)])

def verify_ncp_checksum(data):
    """Verify NCP frame checksum."""
    if len(data) < 2: return False
    total = sum((b if b < 128 else b - 256) for b in data)
    return (total & 0xFF) == 0

# ─── hidraw helpers ───────────────────────────────────────────────────────────
def find_ntrig_hidraw():
    results = []
    for path in sorted(glob.glob('/dev/hidraw*')):
        try:
            fd = os.open(path, os.O_RDWR)
            try:
                buf = array.array('B', [0]*8)
                fcntl.ioctl(fd, HIDIOCGRAWINFO, buf)
                bt  = struct.unpack_from('<I', buf, 0)[0]
                vid = struct.unpack_from('<H', buf, 4)[0]
                pid = struct.unpack_from('<H', buf, 6)[0]
                if vid == NTRIG_VID:
                    results.append((path, vid, pid, bt))
            finally:
                os.close(fd)
        except (OSError, PermissionError):
            pass
    return results

def get_report_descriptor(fd):
    dbuf = array.array('i', [0])
    fcntl.ioctl(fd, HIDIOCGRDESCSIZE, dbuf)
    dsz = dbuf[0]
    ddesc = array.array('B', [0]*4100)
    struct.pack_into('<I', ddesc, 0, dsz)
    fcntl.ioctl(fd, HIDIOCGRDESC, ddesc)
    return bytes(ddesc[4:4+dsz])

def parse_report_sizes(rdesc):
    """Parse HID report descriptor to extract report IDs and their feature report sizes.
    Returns dict: {report_id: {'feature_size': bytes, 'input_size': bytes, 'output_size': bytes}}
    """
    i = 0
    current_rid = 0
    report_size = 0
    report_count = 0
    usage_page = 0
    usage = 0
    reports = {}

    while i < len(rdesc):
        b = rdesc[i]
        tag = (b >> 4) & 0xF
        typ = (b >> 2) & 0x3
        size = b & 0x3
        if size == 3: size = 4
        i += 1
        val = 0
        for j in range(size):
            if i + j < len(rdesc):
                val |= rdesc[i+j] << (8*j)
        i += size

        if typ == 1:  # Global
            if tag == 0:  usage_page = val
            elif tag == 7: report_size = val
            elif tag == 8:
                current_rid = val
                if current_rid not in reports:
                    reports[current_rid] = {'feature_bits': 0, 'input_bits': 0, 'output_bits': 0,
                                            'usage_page': usage_page}
            elif tag == 9: report_count = val
        elif typ == 0:  # Local
            if tag == 0: usage = val
        elif typ == 0 and tag == 5:  # Long tag - skip
            pass
        elif typ == 2:  # Main
            if current_rid not in reports:
                reports[current_rid] = {'feature_bits': 0, 'input_bits': 0, 'output_bits': 0,
                                        'usage_page': usage_page}
            total_bits = report_size * report_count
            if tag == 9:   reports[current_rid]['input_bits'] += total_bits
            elif tag == 8: reports[current_rid]['output_bits'] += total_bits
            elif tag == 11: reports[current_rid]['feature_bits'] += total_bits

    # Convert bits to bytes (rounded up) + 1 for report ID
    result = {}
    for rid, r in reports.items():
        result[rid] = {
            'feature': (r['feature_bits'] + 7) // 8 + 1 if r['feature_bits'] else 0,
            'input':   (r['input_bits'] + 7) // 8 + 1 if r['input_bits'] else 0,
            'output':  (r['output_bits'] + 7) // 8 + 1 if r['output_bits'] else 0,
            'usage_page': r.get('usage_page', 0),
        }
    return result

def try_get(fd, rid, sz):
    buf = bytearray(sz); buf[0] = rid
    try:
        fcntl.ioctl(fd, HIDIOCGFEATURE(sz), buf)
        return True, bytes(buf)
    except OSError as e:
        return False, str(e)

def try_set(fd, data):
    buf = bytearray(data)
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)
        return True, None
    except OSError as e:
        return False, str(e)

# ─── NCP send via report 0x05 (CHUNKED protocol) ─────────────────────────────
# Discovered from DLL function 0x18000cc80:
# Each HID write = 61 bytes: [0x05][remaining_chunks][59 bytes NCP data]
# remaining_chunks counts DOWN from N to 0 (0 = last chunk)

NCP_CHUNK_SIZE = 59   # payload bytes per chunk
NCP_REPORT_ID  = 0x05
NCP_REPORT_LEN = 61   # 1 (report_id) + 1 (remaining) + 59 (data)

def send_ncp_chunked(fd, ncp_frame, verbose=True):
    """Send NCP frame via report 0x05 in 59-byte chunks (DLL alternate path).
    
    Format per chunk: [0x05][remaining_count][59 bytes]
    remaining_count = (total_chunks - 1 - i), so last chunk = 0
    """
    num_full = len(ncp_frame) // NCP_CHUNK_SIZE
    remainder = len(ncp_frame) % NCP_CHUNK_SIZE
    has_partial = 1 if remainder else 0
    total_chunks = num_full + has_partial

    if verbose:
        info(f"Sending {len(ncp_frame)}-byte NCP frame in {total_chunks} chunk(s) via report 0x05")

    for i in range(num_full):
        remaining = total_chunks - 1 - i
        report = bytearray(NCP_REPORT_LEN)
        report[0] = NCP_REPORT_ID
        report[1] = remaining
        report[2:NCP_REPORT_LEN] = ncp_frame[i*NCP_CHUNK_SIZE : (i+1)*NCP_CHUNK_SIZE]
        ok_s, err = try_set(fd, report)
        if not ok_s:
            fail(f"  Chunk {i}/{total_chunks}: SET failed: {err}")
            return False
        if verbose:
            print(f"  Chunk {i+1}/{total_chunks} (remaining={remaining}): {report[:10].hex()}...")

    if remainder:
        remaining = 0
        report = bytearray(NCP_REPORT_LEN)
        report[0] = NCP_REPORT_ID
        report[1] = remaining
        report[2:2+remainder] = ncp_frame[num_full*NCP_CHUNK_SIZE:]
        ok_s, err = try_set(fd, report)
        if not ok_s:
            fail(f"  Final partial chunk: SET failed: {err}")
            return False
        if verbose:
            print(f"  Chunk {total_chunks}/{total_chunks} (remaining=0, partial {remainder}b): {report[:10].hex()}...")

    return True

def poll_all_reports(fd, report_sizes, before_snapshot, label=""):
    """Read all known reports and check for NCP markers or changes."""
    changed = {}
    ncp_found = {}
    for rid, sizes in report_sizes.items():
        sz = sizes['feature']
        if sz < 2: continue
        ok_r, data = try_get(fd, rid, sz)
        if ok_r:
            if NCP_MARKER in data[1:]:  # skip report_id byte
                off = list(data[1:]).index(NCP_MARKER) + 1
                ncp_found[rid] = (data, off)
            before = before_snapshot.get(rid)
            if before and data != before:
                changed[rid] = (before, data)
    return changed, ncp_found

# ─── Main diagnostics ─────────────────────────────────────────────────────────
def run_diagnostics(device_path):
    step("DIAGNOSTICS v5 — Full HID descriptor parse + chunked NCP send")

    fd = os.open(device_path, os.O_RDWR)
    try:
        buf = array.array('B', [0]*8)
        fcntl.ioctl(fd, HIDIOCGRAWINFO, buf)
        bt  = struct.unpack_from('<I', buf, 0)[0]
        vid = struct.unpack_from('<H', buf, 4)[0]
        pid = struct.unpack_from('<H', buf, 6)[0]
        info(f"Bus: {bt} (0x18=I2C)  VID: 0x{vid:04X}  PID: 0x{pid:04X}")

        # ── Parse report descriptor ──
        step("REPORT DESCRIPTOR PARSE")
        rdesc = get_report_descriptor(fd)
        report_sizes = parse_report_sizes(rdesc)
        info(f"Descriptor: {len(rdesc)} bytes, {len(report_sizes)} report IDs")
        print()
        for rid in sorted(report_sizes.keys()):
            s = report_sizes[rid]
            up = s['usage_page']
            vendor = " [VENDOR]" if up >= 0xFF00 or up == 0xFF else ""
            print(f"  Report 0x{rid:02X}: feature={s['feature']}b  "
                  f"input={s['input']}b  output={s['output']}b  "
                  f"usage_page=0x{up:04X}{vendor}")

        # Special attention to reports relevant for NCP
        r05 = report_sizes.get(0x05, {})
        r0b = report_sizes.get(0x0B, {})
        r0c = report_sizes.get(0x0C, {})
        print()
        info(f"Report 0x05 (NCP write candidate): feature={r05.get('feature','?')}b")
        info(f"Report 0x0B (NCP response candidate): feature={r0b.get('feature','?')}b")
        info(f"Report 0x0C (NCP response candidate): feature={r0c.get('feature','?')}b")

        # ── GET_FEATURE baseline snapshot ──
        step("BASELINE GET_FEATURE SNAPSHOT")
        snapshot = {}
        working = {}
        for rid in sorted(report_sizes.keys()):
            sz = report_sizes[rid]['feature']
            if sz < 2: continue
            ok_r, data = try_get(fd, rid, sz)
            if ok_r:
                nz = sum(1 for b in data[1:] if b != 0)
                tag = f"{GREEN}data({nz}nz){RESET}" if nz else f"{YELLOW}zeros{RESET}"
                print(f"  0x{rid:02X}  sz={sz:3d}  {tag}  {data[:16].hex()}")
                snapshot[rid] = data
                working[rid] = sz

        # ── Also try undeclared reports 0x29-0x2D (the I2C NCP reports from DLL) ──
        step("PROBE UNDECLARED REPORTS 0x29-0x2D (I2C NCP channel per DLL analysis)")
        info("These are the hardcoded I2C report IDs from NCPTransportInterface.dll")
        info("They're absent from the SP3 Linux descriptor but might exist on Windows")
        print()
        undeclared_sizes = {0x29: 16, 0x2A: 32, 0x2B: 63, 0x2C: 255, 0x2D: 511}
        for rid, sz in undeclared_sizes.items():
            ok_r, data = try_get(fd, rid, sz)
            if ok_r:
                nz = sum(1 for b in data[1:] if b != 0)
                ok(f"0x{rid:02X} at sz={sz}: RESPONDS! {data[:16].hex()} ({nz}nz)")
                snapshot[rid] = data
                working[rid] = sz
                report_sizes[rid] = {'feature': sz, 'input': 0, 'output': 0, 'usage_page': 0xFF}
            else:
                print(f"  0x{rid:02X}  sz={sz:3d}  {DIM}no response{RESET}")

        # ── MAIN TEST: Chunked NCP via report 0x05 ──
        step("NCP via REPORT 0x05 CHUNKED PROTOCOL (DLL alternate path)")
        info("Protocol: [0x05][remaining_chunks][59 bytes] = 61 bytes per write")
        info("This path is activated in NCPTransportInterface.dll when [this+0x7c]=1")
        print()

        ncp_get_status = build_ncp_frame(0x20, 0x0B, module_id=0x0001)
        ncp_start_cal  = build_ncp_frame(0x20, 0x0A, module_id=0x0001)

        info(f"NCP GET_STATUS frame ({len(ncp_get_status)}b): {ncp_get_status.hex()}")
        info(f"NCP START_CALIB frame ({len(ncp_start_cal)}b): {ncp_start_cal.hex()}")
        print()

        # Send GET_STATUS via chunked protocol
        info("--- Sending NCP GET_STATUS via chunked report 0x05 ---")
        ok_send = send_ncp_chunked(fd, ncp_get_status, verbose=True)
        if ok_send:
            ok("Chunked send succeeded (kernel accepted the write)")
            time.sleep(0.1)
            changed, ncp_found = poll_all_reports(fd, report_sizes, snapshot)
            if ncp_found:
                for rid, (data, off) in ncp_found.items():
                    ok(f"NCP RESPONSE on report 0x{rid:02X} at offset {off}!")
                    hexdump(data[off:off+32])
            elif changed:
                info("Reports changed after chunked send:")
                for rid, (before, after) in changed.items():
                    print(f"  0x{rid:02X}: {before[:16].hex()} → {after[:16].hex()}")
                    snapshot[rid] = after
            else:
                warn("No reports changed after chunked GET_STATUS send")
        else:
            fail("Chunked send failed")

        # Also try with a single-byte NCP frame aligned to chunk boundary
        print()
        info("--- Sending NCP START_CALIB via chunked report 0x05 ---")
        ok_send = send_ncp_chunked(fd, ncp_start_cal, verbose=True)
        if ok_send:
            ok("Sent START_CALIB")
            time.sleep(0.5)  # Give device time to start calibration
            info("Polling for response (10x 500ms)...")
            for i in range(10):
                time.sleep(0.5)
                changed, ncp_found = poll_all_reports(fd, report_sizes, snapshot)
                if ncp_found:
                    for rid, (data, off) in ncp_found.items():
                        ok(f"Poll {i+1}: NCP RESPONSE on 0x{rid:02X}!")
                        hexdump(data[off:off+32])
                        # Check for calibration status
                        frame = data[off:]
                        if len(frame) > 8 and frame[6] == 0x20:
                            if frame[7] == 0x0B:
                                payload = frame[14:-1] if len(frame) > 15 else b''
                                if payload[:3] == b'\x42\x42\x42':
                                    ok("STATUS: 'BBB' = CALIBRATION COMPLETE!")
                                elif payload[:3] == b'\x63\x63\x63':
                                    info("STATUS: 'ccc' = in progress...")
                                elif payload[:3] == b'\x21\x21\x21':
                                    info("STATUS: '!!!' = unknown/waiting")
                        snapshot.update({rid: data for rid, (_, data) in changed.items()})
                        break
                elif changed:
                    info(f"  Poll {i+1}: Report changed: " +
                         ", ".join(f"0x{r:02X}" for r in changed))
                    snapshot.update({rid: after for rid, (_, after) in changed.items()})
                else:
                    print(f"    Poll {i+1}: no change")

        # ── Alternative: try writing NCP directly to report 0x05 without chunking ──
        step("NCP DIRECT (non-chunked) via report 0x05 — in case chunking not needed")
        info("Some firmware variants accept NCP directly in feature report payload")
        r05_sz = report_sizes.get(0x05, {}).get('feature', 64)
        if r05_sz < 2: r05_sz = 64
        report = bytearray(r05_sz)
        report[0] = 0x05
        report[1:1+len(ncp_get_status)] = ncp_get_status
        ok_s, err = try_set(fd, report)
        if ok_s:
            ok(f"SET 0x05 at sz={r05_sz}: accepted")
            time.sleep(0.1)
            changed, ncp_found = poll_all_reports(fd, report_sizes, snapshot)
            if ncp_found:
                for rid, (data, off) in ncp_found.items():
                    ok(f"NCP RESPONSE on 0x{rid:02X}!")
                    hexdump(data[off:off+32])
            elif changed:
                info("Changed: " + ", ".join(f"0x{r:02X}" for r in changed))
        else:
            warn(f"SET 0x05 direct failed: {err}")

        # ── Check if there's an NCP response via async input report ──
        step("ASYNC INPUT REPORT READ (brief)")
        info("The DLL's receive thread reads HID input reports (not feature reports)")
        info("Trying non-blocking read from hidraw after sending...")
        import select
        # Set non-blocking
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Send GET_STATUS and immediately try to read
        send_ncp_chunked(fd, ncp_get_status, verbose=False)
        time.sleep(0.2)
        for _ in range(5):
            ready = select.select([fd], [], [], 0.3)[0]
            if ready:
                try:
                    data = os.read(fd, 512)
                    nz = sum(1 for b in data[1:] if b != 0)
                    ok(f"Input report received! {len(data)}b ({nz}nz): {data[:24].hex()}")
                    if NCP_MARKER in data:
                        off = list(data).index(NCP_MARKER)
                        ok(f"NCP marker at offset {off}!")
                        hexdump(data[off:off+32])
                except OSError:
                    pass
            else:
                print("    (no input data)")

        # Restore blocking
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)

        step("DONE")
        info("If nothing responded above, next steps:")
        info("  1. Boot Windows, run CalibG4.exe with ETW/Wireshark HID capture")
        info("  2. Check if SP3 has USB N-Trig device (0x1B96:0x0022) under Windows")
        info("  3. Use HID-BPF to intercept kernel I2C-HID traffic")
        info("  4. Check /dev/hidraw0 if exists (might be separate HID collection)")

    finally:
        os.close(fd)


# ─── Raw I2C (unchanged from v4, kept for reference) ──────────────────────────
I2C_RDWR = 0x0707
I2C_M_RD = 0x0001

class i2c_msg(ctypes.Structure):
    _fields_ = [('addr', ctypes.c_ushort), ('flags', ctypes.c_ushort),
                 ('len', ctypes.c_ushort), ('buf', ctypes.POINTER(ctypes.c_ubyte))]

class i2c_rdwr_ioctl_data(ctypes.Structure):
    _fields_ = [('msgs', ctypes.POINTER(i2c_msg)), ('nmsgs', ctypes.c_uint)]

def i2c_write_read(fd, addr, wdata, rlen):
    wbuf = (ctypes.c_ubyte * len(wdata))(*wdata)
    rbuf = (ctypes.c_ubyte * rlen)()
    msgs = (i2c_msg * 2)(
        i2c_msg(addr=addr, flags=0, len=len(wdata),
                buf=ctypes.cast(wbuf, ctypes.POINTER(ctypes.c_ubyte))),
        i2c_msg(addr=addr, flags=I2C_M_RD, len=rlen,
                buf=ctypes.cast(rbuf, ctypes.POINTER(ctypes.c_ubyte))))
    rdwr = i2c_rdwr_ioctl_data(
        msgs=ctypes.cast(msgs, ctypes.POINTER(i2c_msg)), nmsgs=2)
    fcntl.ioctl(fd, I2C_RDWR, rdwr)
    return bytes(rbuf)

def i2c_write(fd, addr, wdata):
    wbuf = (ctypes.c_ubyte * len(wdata))(*wdata)
    msgs = (i2c_msg * 1)(
        i2c_msg(addr=addr, flags=0, len=len(wdata),
                buf=ctypes.cast(wbuf, ctypes.POINTER(ctypes.c_ubyte))))
    rdwr = i2c_rdwr_ioctl_data(
        msgs=ctypes.cast(msgs, ctypes.POINTER(i2c_msg)), nmsgs=1)
    fcntl.ioctl(fd, I2C_RDWR, rdwr)

def find_i2c_info():
    for dev_path in glob.glob('/sys/bus/i2c/devices/*'):
        basename = os.path.basename(dev_path)
        is_ntrig = False
        for f in ['name', 'uevent']:
            fp = os.path.join(dev_path, f)
            if os.path.exists(fp):
                try:
                    with open(fp) as fh:
                        if 'NTRG' in fh.read():
                            is_ntrig = True; break
                except OSError:
                    pass
        if not is_ntrig:
            continue
        real = os.path.realpath(dev_path)
        m = re.search(r'/i2c-(\d+)/', real)
        bus = int(m.group(1)) if m else None
        addr = None
        for child in glob.glob(os.path.join(real, '*')):
            cb = os.path.basename(child)
            m2 = re.match(r'^(\d+)-([0-9a-fA-F]{4})$', cb)
            if m2:
                addr = int(m2.group(2), 16)
                break
        if addr is None:
            addr = 0x07
            warn(f"  Guessing addr=0x{addr:02X}")
        driver = None
        drv_link = os.path.join(dev_path, 'driver')
        if os.path.islink(drv_link):
            driver = os.path.basename(os.readlink(drv_link))
        return bus, addr, dev_path, basename, driver
    return None, None, None, None, None


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='N-Trig calibration v5 for SP3 (Linux)')
    p.add_argument('-d', '--device', help='hidraw device (default: auto-detect)')
    p.add_argument('--list', action='store_true', help='list all N-Trig hidraw devices')
    p.add_argument('--diag', action='store_true', help='run full diagnostics (default)')
    p.add_argument('--calibrate', action='store_true',
                   help='send START_CALIB only (use after confirming NCP channel works)')
    p.add_argument('--module-id', type=lambda x: int(x, 0), default=0x0001)
    args = p.parse_args()

    if os.geteuid() != 0:
        print(f"{RED}Requires root.{RESET}"); sys.exit(1)

    devices = find_ntrig_hidraw()
    if not devices:
        fail("No N-Trig hidraw devices found"); sys.exit(1)

    if args.list:
        for path, v, pid, bt in devices:
            info(f"{path}: 0x{v:04X}:0x{pid:04X}  bus={'I2C' if bt==0x18 else 'USB' if bt==3 else bt}")
        sys.exit(0)

    # Show ALL N-Trig devices - important! SP3 might have multiple collections
    if len(devices) > 1:
        warn(f"Multiple N-Trig hidraw devices found!")
        for path, v, pid, bt in devices:
            info(f"  {path}: 0x{v:04X}:0x{pid:04X}  bus={'I2C' if bt==0x18 else 'USB' if bt==3 else bt}")

    dev = args.device or devices[0][0]
    ok(f"Using {dev}")

    run_diagnostics(dev)


if __name__ == '__main__':
    main()
</file>
</assistant>
