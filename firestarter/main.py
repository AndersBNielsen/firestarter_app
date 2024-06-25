#!/usr/bin/env python
"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.
"""

import sys
import time
import serial
import serial.tools.list_ports
import os
import json
import argparse
import requests

try:
    from . import database as db
    from .avr_tool import Avrdude
except ImportError:
    import database as db
    from avr_tool import Avrdude


BAUD_RATE = "115200"

STATE_READ = 1
STATE_WRITE = 2
STATE_ERASE = 3
STATE_CHECK_BLANK = 4
STATE_READ_VPE = 10
STATE_READ_VPP = 11
STATE_READ_VCC = 12
STATE_VERSION = 13
STATE_CONFIG = 14

FIRESTARTER_RELEASE_URL = (
    "https://api.github.com/repos/henols/firestarter/releases/latest"
)

HOME_PATH = os.path.join(os.path.expanduser("~"), ".firestarter")
CONFIG_FILE = os.path.join(HOME_PATH, "config.json")


def open_config():
    global config
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as file:
            config = json.load(file)


def save_config():
    if not os.path.exists(HOME_PATH):
        os.makedirs(HOME_PATH)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)


def check_port(port, data):
    try:
        if verbose:
            print(f"Check port: {port}")

        ser = serial.Serial(
            port,
            BAUD_RATE,
            timeout=1.0,
            # inter_byte_timeout=0.1,
        )
        time.sleep(2)
        ser.write(data.encode("ascii"))
        ser.flush()

        res, msg = wait_for_response(ser)
        if res == "OK":
            return ser
        else:
            print(msg)
    except (OSError, serial.SerialException):
        pass

    return None


def find_comports():
    ports = []
    if "port" in config.keys():
        ports.append(config["port"])

    serial_ports = serial.tools.list_ports.comports()
    for port in serial_ports:
        if (
            "Arduino" in port.manufacturer or "FTDI" in port.manufacturer
        ) and not port.device in ports:
            ports.append(port.device)
    return ports


def find_programmer(data):
    if verbose:
        print("Config data:")
        print(data)

    ports = find_comports()
    for port in ports:
        serial_port = check_port(port, data)
        if serial_port:
            config["port"] = port
            save_config()
            return serial_port
    return None


def wait_for_response(ser):
    timeout = time.time()
    while True:
        # time.sleep(1)
        byte_array = ser.readline()
        res = read_filterd_bytes(byte_array)
        if res and len(res) > 0:
            if "OK:" in res:
                msg = res[res.rfind("OK:") :]
                write_feedback(msg)
                return "OK", msg[3:].strip()
            elif "WARN:" in res:
                msg = res[res.rfind("WARN:") :]
                write_feedback(msg)
                return "WARN", msg[5:].strip()
            elif "ERROR:" in res:
                msg = res[res.rfind("ERROR:") :]
                write_feedback(msg)
                return "ERROR", msg[6:].strip()
            elif "DATA:" in res:
                msg = res[res.rfind("DATA:") :]
                write_feedback(msg)
                return "DATA", msg[5:].strip()
            elif "INFO:" in res:
                write_feedback(res[res.rfind("INFO:") :])
            # else:
            #     print(res)
            timeout = time.time()

        # elif len(byte_array) > 0:
        #     print(len(byte_array))
        if timeout + 5 < time.time():
            print(" --  Timeout")
            return "ERROR", "Timeout"


def write_feedback(msg):
    if verbose:
        print(msg)


def print_progress(p, from_address, to_address):
    if verbose:
        print(f"{p}%, address: 0x{from_address:X} - 0x{to_address:X} ")
    else:
        print(f"\r{p}%, address: 0x{from_address:X} - 0x{to_address:X} ", end="")


def read_filterd_bytes(byte_array):
    res = [b for b in byte_array if 32 <= b <= 126]
    if res:
        return "".join([chr(b) for b in res])
    else:
        return None


def list_eproms(verifed):
    if not all:
        print("Verified EPROMS in the database.")
    for ic in db.get_eproms(verifed):
        print(ic)


def search_eproms(text):
    print(f"Searching for: {text}")
    # if not all:
    #     print("Verified EPROMS in the database.")

    for ic in db.search_eprom(text, True):
        print(ic)


def eprom_info(name):
    eprom = db.get_eprom(name)
    if not eprom:
        print(f"Eprom {name} not found.")
        return
    
    verified =""
    if not eprom["verified"]:
        verified = "\t-- NOT VERIFIED --"

    print(f"Eprom Info {verified}")
    print(f"Name:\t\t{eprom['name']}")
    print(f"Manufacturer:\t{eprom['manufacturer']}")
    print(f"Number of pins:\t{eprom['pin-count']}")
    print(f"Memory size:\t{hex(eprom['memory-size'])}")
    if eprom["type"] == 1:
        print(f"Type:\t\tEPROM")
        print(f"Can be erased:\t{eprom['can-erase']}")
        if eprom["has-chip-id"]:
            print(f"Chip ID:\t{hex(eprom['chip-id'])}")
        print(f"VPP:\t\t{eprom['vpp']}")
    elif eprom["type"] == 2:
        print(f"Type:\t\tSRAM")
    print(f"Pulse delay:\t{eprom['pulse-delay']}µS")


def read_voltage(state):
    data = {}
    data["state"] = state
    data = json.dumps(data)
    # print(data)
    ser = find_programmer(data)
    if not ser:
        print("No programmer found")
        return
    ser.write("OK".encode("ascii"))
    type = "VPE"
    if state == STATE_READ_VCC:
        type = "VCC"
    if state == STATE_READ_VPP:
        type = "VPP"

    print(f"Reading {type} voltage")
    while (t := wait_for_response(ser))[0] == "DATA":
        print(f"\r{t[1]}", end="")
        ser.write("OK".encode("ascii"))


def firmware(install, avrdude_path):
    latest, port, url = firmware_check()
    if not latest and install:
        if not url:
            latest_version, url = latest_firmware()
            print(f"Trying to install firmware version: {latest_version}")
        install_firmware(url, avrdude_path, port)


def firmware_check():
    # if not install:
    data = {}
    data["state"] = STATE_VERSION
    data = json.dumps(data)
    # print(data)
    ser = find_programmer(data)
    if not ser:
        print("No programmer found")
        return False, None, None
    ser.write("OK".encode("ascii"))
    print("Reading version")
    r, version = wait_for_response(ser)
    ser.close()

    if r == "OK":
        print(f"Firmware version: {version}")
    else:
        print(r)
        return False, None, None
    latest_version, url = latest_firmware()
    major, minor, patch = version.split(".")

    major_l, minor_l, patch_l = latest_version.split(".")

    if (
        int(major) < int(major_l)
        or int(minor) < int(minor_l)
        or int(patch) < int(patch_l)
    ):
        print(f"New version available: {latest_version}")
        return False, ser.portstr, url
    else:
        print("You have the latest version")
        return True, None, None


def install_firmware(url, avrdude_path, port=None):

    if port:
        ports = [port]
    else:
        ports = find_comports()
        if len(ports) == 0:
            print("No Arduino found")
            return

    for port in ports:
        try:
            if not avrdude_path:
                if "avrdude-path" in config.keys():
                    avrdude_path = config["avrdude-path"]

            a = Avrdude(
                partno="ATmega328P",
                programmer_id="arduino",
                baud_rate="115200",
                port=port,
                avrdudePath=avrdude_path,
            )
        except FileNotFoundError:
            print("Avrdude not found")
            print("Full path to avrdude needs to be provided --avrdude-path")
            return

        output, error, returncode = a.testConnection()
        if returncode == 0:
            print(f"Found programmer at port: {port}")
            print("Downloading firmware...")
            response = requests.get(url)
            if not response.status_code == 200:
                print("Error downloading firmware")
                return

            firmware_path = os.path.join(HOME_PATH, "firmware.hex")
            with open(firmware_path, "wb") as f:
                f.write(response.content)
            print("Firmware downloaded")
            print("Installing firmware")
            output, error, returncode = a.flashFirmware(firmware_path)
            if returncode == 0:
                print("Firmware updated")
            else:
                print("Error updating firmware")
                print(str(error, "ascii"))
                return
            if avrdude_path:
                config["avrdude-path"] = avrdude_path
                save_config()
            return
        else:
            print(f"Error connecting to programmer at port: {port}")
            print(str(error, "ascii"))
            continue
        # if output:

    print("Please reset the programmer to start the update")
    return


def latest_firmware():
    response = requests.get(FIRESTARTER_RELEASE_URL)
    if not response.status_code == 200:
        return None, None
    latest = response.json()
    latest_version = latest["tag_name"]
    for asset in latest["assets"]:
        if "firmware.hex" in asset["name"]:
            return latest_version, asset["browser_download_url"]
    return None, None


def rurp_config(vcc=None, r1=None, r2=None):
    data = {}
    data["state"] = STATE_CONFIG
    if vcc:
        data["vcc"] = vcc
    if r1:
        data["r1"] = r1
    if r2:
        data["r2"] = r2
    data = json.dumps(data)
    ser = find_programmer(data)
    if not ser:
        print("No programmer found")
        return
    ser.write("OK\n".encode("ascii"))
    print("Reading configuration")
    r, version = wait_for_response(ser)
    if r == "OK":
        print(f"Config: {version}")
    else:
        print(r)


def read_chip(eprom, output_file, port=None):
    data = db.get_eprom(eprom)
    if not data:
        print(f"Eprom {eprom} not found.")
        return
    eprom = data.pop("name")
    data.pop("manufacturer")
    data.pop("verified")
    mem_size = data["memory-size"]
    # data.pop("memory-size")

    # data.pop("has-chip-id")

    # data.pop("bus-config")
    # data["bus-config"].pop("bus")

    data["state"] = STATE_READ
    data = json.dumps(data)

    ser = find_programmer(data)
    if not ser:
        print("No programmer found")
        return
    print(f"Reading chip: {eprom}")
    if not output_file:
        output_file = f"{eprom}.bin"
    print(f"Output will be saved to: {output_file}")
    bytes_read = 0
    try:
        ser.write("OK".encode("ascii"))
        ser.flush()
        output_file = open(output_file, "wb")
        start_time = time.time()

        while True:
            match (t := wait_for_response(ser))[0]:

                case "DATA":
                    serial_data = ser.read(256)
                    output_file.write(serial_data)
                    bytes_read += 256
                    p = int(bytes_read / mem_size * 100)
                    print_progress(p, bytes_read - 256, bytes_read)
                    ser.write("OK\n".encode("ascii"))
                    ser.flush()
                case "OK":
                    print()
                    print("Finished reading data")
                    break
                case _:
                    print(f"Error reading data {t[1]}")
                    print(wait_for_response(ser)[1])
                    return

        end_time = time.time()
        # Calculate total duration
        total_duration = end_time - start_time
        print(f"File recived in {total_duration:.2f} seconds")

    except Exception as e:
        print("Error:", e)
    finally:
        output_file.close()
        ser.close()


def write_chip(eprom, input_file, port=None, address=None):
    data = db.get_eprom(eprom)
    if not data:
        print(f"Eprom {eprom} not found.")
        return
    if not os.path.exists(input_file):
        print(f"File {input_file} not found.")
        return
    file_size = os.path.getsize(input_file)
    eprom = data.pop("name")
    data.pop("manufacturer")
    data.pop("verified")
    # data["has-chip-id"] = False
    # data["can-erase"] = False
    if address:
        if "0x" in address:
            data["address"] = int(address, 16)
        else:
            data["address"] = int(address)

    data["state"] = STATE_WRITE
    json_data = json.dumps(data)
    mem_size = data["memory-size"]
    if not mem_size == file_size:
        print(f"The file size dont match the memory size")
    
    start_time = time.time()
    
    ser = find_programmer(json_data)
    if not ser:
        print("No programmer found")
        return

    print(f"Writing to chip: {eprom}")
    print(f"Reading from input file: {input_file}")
    bytes_sent = 0
    block_size = 256

    # Open the file to send
    with open(input_file, "rb") as f:
       
        print(f"Sending file {input_file} in blocks of {block_size} bytes")

        # Read the file and send in blocks
        while True:
            data = f.read(block_size)
            if not data:
                ser.write(int(0).to_bytes(2) )
                ser.flush()
                resp, info = wait_for_response(ser)
                print("End of file reached")
                print(info)
                return

            ser.write(len(data).to_bytes(2) )
            sent = ser.write(data)
            ser.flush()
            resp, info = wait_for_response(ser)
            if resp == "OK":
                bytes_sent += sent
                p = int(bytes_sent / file_size * 100)
                print_progress(p, bytes_sent - block_size, bytes_sent)
            elif resp == "ERROR":
                print()
                print(f"Error writing: {info}")
                return
            if bytes_sent == mem_size:
                break

    
    # Calculate total duration
    total_duration = time.time() - start_time
    print()
    print(f"File sent successfully in {total_duration:.2f} seconds")


def main():
    global verbose

    parser = argparse.ArgumentParser(description="EPROM programer for Arduiono UNO and Relatively-Universal-ROM-Programmer sheild.")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose mode"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Read command
    read_parser = subparsers.add_parser("read", help="Reads the content from an EPROM.")
    read_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
    read_parser.add_argument(
        "output_file",
        nargs="?",
        type=str,
        help="Output file name (optional), defaults to the EPROM_NAME.bin",
    )
    read_parser.add_argument(
        "-p", "--port", type=str, help="Serial port name (optional)"
    )

    # Write command
    write_parser = subparsers.add_parser(
        "write", help="Writes a binary file to an EPROM."
    )
    write_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
    # write_parser.add_argument(
    #     "-f", "--force", action="store_true", help="Force the write operation"
    # )
    write_parser.add_argument(
        "-a", "--address", type=str, help="Write start address in dec/hex"
    )
    write_parser.add_argument(
        "-p", "--port", type=str, help="Serial port name (optional)"
    )
    write_parser.add_argument("input_file", type=str, help="Input file name")

    # List command
    list_parser = subparsers.add_parser(
        "list", help="List all EPROMs in the database."
    )
    list_parser.add_argument(
        "-v", "--verified", action="store_true", help="Only shows verifed EPROMS"
    )

    # Search command
    search_parser = subparsers.add_parser(
        "search", help="Search for EPROMs in the database."
    )
    search_parser.add_argument("text", type=str, help="Text to search for")

    # Info command
    info_parser = subparsers.add_parser("info", help="EPROM info.")
    info_parser.add_argument("eprom", type=str, help="EPROM name.")

    # vpe_parser = subparsers.add_parser("vpe", help="VPE voltage.")
    vpp_parser = subparsers.add_parser("vpp", help="VPP voltage.")
    vcc_parser = subparsers.add_parser("vcc", help="VCC voltage.")

    fw_parser = subparsers.add_parser("fw", help="FIRMWARE version.")
    fw_parser.add_argument(
        "-i",
        "--install",
        action="store_true",
        help="Try to install the latest firmware.",
    )
    fw_parser.add_argument(
        "-p",
        "--avrdude-path",
        type=str,
        help="Full path to avrdude (optional), set if avrdude is not found.",
    )
    fw_parser.add_argument("--port", type=str, help="Serial port name (optional)")

    config_parser = subparsers.add_parser(
        "config", help="Handles CONFIGURATION values."
    )
    config_parser.add_argument(
        "-v", "--vcc", type=float, help="Set Arduino VCC voltage."
    )

    config_parser.add_argument(
        "-r1", "--r16", type=int, help="Set R16 resistance, resistor connected to VPE"
    )
    config_parser.add_argument(
        "-r2",
        "--r14r15",
        type=int,
        help="Set R14/R15 resistance, resistors connected to GND",
    )
    config_parser.add_argument(
        "-p", "--port", type=str, help="Serial port name (optional)"
    )

    if len(sys.argv) == 1:
        args = parser.parse_args(["--help"])
    else:
        args = parser.parse_args()

    open_config()

    verbose = args.verbose
    db.init()

    if args.command == "list":
        list_eproms(args.verified)
    elif args.command == "info":
        eprom_info(args.eprom)
    elif args.command == "search":
        search_eproms(args.text)
    elif args.command == "read":
        read_chip(args.eprom, args.output_file, port=None)
    elif args.command == "write":
        write_chip(args.eprom, args.input_file, port=None, address=args.address)
    elif args.command == "vpe":
        read_voltage(STATE_READ_VPE)
    elif args.command == "vpp":
        read_voltage(STATE_READ_VPP)
    elif args.command == "vcc":
        read_voltage(STATE_READ_VCC)
    elif args.command == "fw":
        firmware(args.install, args.avrdude_path)
    elif args.command == "config":
        rurp_config(args.vcc, args.r16, args.r14r15)


if __name__ == "__main__":
    main()