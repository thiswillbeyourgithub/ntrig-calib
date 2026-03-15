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
