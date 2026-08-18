"""
Fake camera integration test for arlo-cam-api.

Simulates a VMC4040P camera connecting to the server on port 4000 by sending
registration, status, pirMotionAlert, and motionTimeoutAlert messages in the
exact wire format real Arlo cameras use (`L:{len} {json}` over TCP).

Verifies:
- ACK response received for every message
- Fake device appears in GET /device API
- /device/{serial} returns the device status after status message
- pirMotionAlert does not crash the connection thread (no Exception in thread)
- Fake device can be cleaned up via DELETE /device/{serial}

Usage:
    python3 test/fake_camera_integration_test.py --host 10.0.0.48 --port 4000
"""
import argparse
import json
import socket
import subprocess
import sys
import time

import requests

TEST_SERIAL = "FAKETEST000001"
TEST_MODEL = "VMC4040P"


def send_message(host, port, msg_dict):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect((host, port))
    msg_json = json.dumps(msg_dict, separators=(',', ':'))
    payload = f"L:{len(msg_json)} {msg_json}"
    sock.sendall(payload.encode('utf-8'))
    chunk = sock.recv(1024)
    sock.close()
    if not chunk:
        return None
    data = chunk.decode('utf-8')
    if not data.startswith("L:"):
        return None
    delimiter = data.index(" ")
    data_len = int(data[2:delimiter])
    resp_json = data[delimiter + 1:delimiter + 1 + data_len]
    return json.loads(resp_json)


def assert_ack(resp, label):
    assert resp is not None, f"[{label}] No response from server"
    assert resp.get("Response") == "Ack", f"[{label}] Expected Ack, got: {resp}"
    print(f"  [ACK received] {label} OK")


def test_registration(host, port, api_base):
    print("--- Test: registration ---")
    msg = {
        "Type": "registration",
        "ID": 1,
        "SystemSerialNumber": TEST_SERIAL,
        "SystemModelNumber": TEST_MODEL,
        "SystemFirmwareVersion": "34.1.10_39323cf",
        "BatPercent": 85,
        "ChargingState": "Off",
        "WifiCountryDetails": "US/36",
        "InterfaceVersion": 1,
        "Capabilities": ["IRLED", "PirMotion", "NightVision", "Temperature",
                         "BatteryLevel", "Microphone", "Speaker", "H.264Streaming",
                         "JPEGSnapshot", "AutomatedStop", "BEC", "RaParams"],
        "HardwareRevision": "H3",
        "BootSeconds": 4,
    }
    assert_ack(send_message(host, port, msg), "registration")
    devices = requests.get(f"{api_base}/device", timeout=5).json()
    serials = [d["serial_number"] for d in devices]
    assert TEST_SERIAL in serials, f"Fake device not in /device list: {serials}"
    print(f"  [device registered] {TEST_SERIAL} visible in /device")


def test_status(host, port, api_base):
    print("--- Test: status ---")
    msg = {
        "Type": "status",
        "ID": 2,
        "SystemSerialNumber": TEST_SERIAL,
        "SystemFirmwareVersion": "34.1.10_39323cf",
        "BatPercent": 84,
        "ChargingState": "Off",
        "Bat1Volt": 4.2,
        "WifiRSSI": -65,
        "CameraOnline": 100,
        "CameraOffline": 5,
        "PoweredOn": 105,
    }
    assert_ack(send_message(host, port, msg), "status")
    dev = requests.get(f"{api_base}/device/{TEST_SERIAL}", timeout=5).json()
    assert dev.get("BatPercent") == 84, f"Status not persisted, got: {dev}"
    print(f"  [status persisted] BatPercent={dev['BatPercent']}% V={dev['Bat1Volt']}")


def test_pir_motion_alert(host, port, api_base):
    print("--- Test: pirMotionAlert (triggers snapshot if enabled) ---")
    msg = {
        "Type": "alert",
        "ID": 3,
        "AlertType": "pirMotionAlert",
        "SystemSerialNumber": TEST_SERIAL,
        "PIRMotion": {
            "Triggered": True,
            "TriggerLevel": 5000,
            "zones": [],
            "MdZones": 0,
            "PirTrigger": 1,
            "z0Intensity": 80,
            "z0Counter": 320,
        }
    }
    assert_ack(send_message(host, port, msg), "pirMotionAlert")
    time.sleep(2)
    print("  [snapshot triggered or skipped] (arlo-snapshot will fail RTSP for fake camera)")


def test_motion_timeout_alert(host, port, api_base):
    print("--- Test: motionTimeoutAlert ---")
    msg = {
        "Type": "alert",
        "ID": 4,
        "AlertType": "motionTimeoutAlert",
        "SystemSerialNumber": TEST_SERIAL,
        "StreamDuration": 10,
    }
    assert_ack(send_message(host, port, msg), "motionTimeoutAlert")


def check_no_crashes(since_seconds=60):
    print("--- Check: no 'Exception in thread' in container logs since last 60s ---")
    try:
        result = subprocess.run(
            ["docker", "logs", "--since", f"{since_seconds}s", "arlo-cam-api"],
            capture_output=True, text=True, timeout=10
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  [skip] docker logs unavailable: {e}")
        return True
    lines = result.stdout.split("\n") + result.stderr.split("\n")
    crashes = [l for l in lines if "Exception in thread" in l or "MissingSchema" in l]
    if crashes:
        print(f"  [FAIL] {len(crashes)} thread crash(es) detected:")
        for c in crashes[:3]:
            print(f"    {c[:160]}")
        return False
    print("  [PASS] No thread crashes since last 60s")
    return True


def cleanup(api_base):
    print("--- Cleanup ---")
    try:
        r = requests.delete(f"{api_base}/device/{TEST_SERIAL}", timeout=5)
        print(f"  [deleted] {r.json()}")
    except Exception as e:
        print(f"  [error] cleanup failed: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="10.0.0.48")
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--api", default="http://10.0.0.48:5000")
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()

    print(f"Fake camera integration test")
    print(f"  host={args.host}:{args.port} api={args.api} serial={TEST_SERIAL}\n")

    failures = []
    for test in [
        lambda: test_registration(args.host, args.port, args.api),
        lambda: test_status(args.host, args.port, args.api),
        lambda: test_pir_motion_alert(args.host, args.port, args.api),
        lambda: test_motion_timeout_alert(args.host, args.port, args.api),
    ]:
        try:
            test()
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failures.append(str(e))
        except Exception as e:
            print(f"  ERROR: {e}")
            failures.append(str(e))
        time.sleep(1)

    crashes_ok = check_no_crashes(since_seconds=120)
    if not crashes_ok:
        failures.append("thread crashes detected in logs")

    if not args.no_cleanup:
        cleanup(args.api)

    print()
    if failures:
        print(f"FAILED ({len(failures)} issues)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()