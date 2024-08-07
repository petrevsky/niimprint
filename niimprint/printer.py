import abc
import enum
import logging
import math
import socket
import struct
import time

import serial
from PIL import Image, ImageOps, ImageDraw, ImageFont

from serial.tools.list_ports import comports as list_comports

from dataclasses import dataclass
from packet import NiimbotPacket


class InfoEnum(enum.IntEnum):
    DENSITY = 1
    PRINTSPEED = 2
    LABELTYPE = 3
    LANGUAGETYPE = 6
    AUTOSHUTDOWNTIME = 7
    DEVICETYPE = 8
    SOFTVERSION = 9
    BATTERY = 10
    DEVICESERIAL = 11
    HARDVERSION = 12


class RequestCodeEnum(enum.IntEnum):
    GET_INFO = 64  # 0x40
    GET_RFID = 26  # 0x1A
    HEARTBEAT = 220  # 0xDC
    SET_LABEL_TYPE = 35  # 0x23
    SET_LABEL_DENSITY = 33  # 0x21
    START_PRINT = 1  # 0x01
    END_PRINT = 243  # 0xF3
    START_PAGE_PRINT = 3  # 0x03
    END_PAGE_PRINT = 227  # 0xE3
    ALLOW_PRINT_CLEAR = 32  # 0x20
    SET_DIMENSION = 19  # 0x13
    SET_QUANTITY = 21  # 0x15
    GET_PRINT_STATUS = 163  # 0xA3


@dataclass
class PrintStatus:
    finished: bool
    progress: int
    error: bool


def _packet_to_int(x):
    return int.from_bytes(x.data, "big")


class BaseTransport(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def read(self, length: int) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def write(self, data: bytes):
        raise NotImplementedError


class TCPTransport(BaseTransport):
    def __init__(self, host: str, port: int):
        print(host)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        try:
            self._sock.connect((host, port))
        except Exception as e:
            print(f"Failed to connect to {host}:{port}: {e}")
            raise

    def read(self, length: int) -> bytes:
        data = b""
        while len(data) < length:
            chunk = self._sock.recv(length - len(data))
            if not chunk:
                raise ConnectionError("Connection closed while reading")
            data += chunk
        return data

    def write(self, data: bytes):
        total_sent = 0
        while total_sent < len(data):
            sent = self._sock.send(data[total_sent:])
            if sent == 0:
                raise ConnectionError("Connection closed while writing")
            total_sent += sent


class SerialTransport(BaseTransport):
    def __init__(self, port: str = "auto"):
        port = port if port != "auto" else self._detect_port()
        self._serial = serial.Serial(port=port, baudrate=115200, timeout=0.5)

    def _detect_port(self):
        all_ports = list(list_comports())
        if len(all_ports) == 0:
            raise RuntimeError("No serial ports detected")
        if len(all_ports) > 1:
            msg = "Too many serial ports, please select specific one:"
            for port, desc, hwid in all_ports:
                msg += f"\n- {port} : {desc} [{hwid}]"
            raise RuntimeError(msg)
        return all_ports[0][0]

    def read(self, length: int) -> bytes:
        return self._serial.read(length)

    def write(self, data: bytes):
        return self._serial.write(data)


class PrinterClient:
    def __init__(self, transport):
        self._transport = transport
        self._packetbuf = bytearray()

    def print_image(self, image: Image, density: int = 3, copies: int = 1):
        self.set_label_density(density)
        self.set_label_type(1)
        self.start_print(copies)
        # self.allow_print_clear()  # Something unsupported in protocol decoding (B21)
        self.start_page_print()
        self.set_dimension(image.height, image.width, copies)
        # self.set_quantity(2)  # Same thing (B21)
        for pkt in self._encode_image(image):
            self._send(pkt)
            time.sleep(0.01)

        self.end_page_print()
        time.sleep(2)

    def _encode_image(self, image: Image):
        img = ImageOps.invert(image.convert("L")).convert("1")
        for y in range(img.height):
            line_data = [img.getpixel((x, y)) for x in range(img.width)]
            line_data = "".join("0" if pix == 0 else "1" for pix in line_data)
            line_data = int(line_data, 2).to_bytes(math.ceil(img.width / 8), "big")
            counts = (0, 0, 0)  # It seems like you can always send zeros
            header = struct.pack(">H3BB", y, *counts, 1)
            pkt = NiimbotPacket(0x85, header + line_data)
            yield pkt

    def _recv(self):
        packets = []
        self._packetbuf.extend(self._transport.read(1024))
        while len(self._packetbuf) > 4:
            pkt_len = self._packetbuf[3] + 7
            if len(self._packetbuf) >= pkt_len:
                packet = NiimbotPacket.from_bytes(self._packetbuf[:pkt_len])
                self._log_buffer("recv", packet.to_bytes())
                packets.append(packet)
                del self._packetbuf[:pkt_len]
        return packets

    def _send(self, packet):
        self._transport.write(packet.to_bytes())

    def _log_buffer(self, prefix: str, buff: bytes):
        msg = ":".join(f"{i:#04x}"[-2:] for i in buff)
        logging.debug(f"{prefix}: {msg}")

    def _transceive(self, reqcode, data, respoffset=1):
        respcode = respoffset + reqcode
        packet = NiimbotPacket(reqcode, data)
        self._log_buffer("send", packet.to_bytes())

        max_retries = 3
        for attempt in range(max_retries):
            try:
                self._transport.write(packet.to_bytes())

                # Read header (4 bytes)
                header = self._transport.read(4)
                if len(header) != 4 or header[:2] != b"\x55\x55":
                    raise ValueError(f"Invalid header: {header.hex()}")

                data_length = header[3]

                # Read rest of the packet
                rest_of_packet = self._transport.read(
                    data_length + 3
                )  # data + checksum + AA AA
                if len(rest_of_packet) != data_length + 3:
                    raise ValueError(
                        f"Incomplete packet: expected {data_length + 3} bytes, got {len(rest_of_packet)}"
                    )

                full_packet = header + rest_of_packet

                try:
                    received_packet = NiimbotPacket.from_bytes(full_packet)
                except ValueError as e:
                    logging.error(f"Error decoding packet: {full_packet.hex()}")
                    raise

                self._log_buffer("recv", full_packet)

                if received_packet.type == 219:
                    raise ValueError("Received error packet")
                elif received_packet.type == 0:
                    raise NotImplementedError("Received unimplemented packet type")
                elif received_packet.type == respcode:
                    return received_packet
                else:
                    raise ValueError(
                        f"Unexpected response code: {received_packet.type}, expected: {respcode}"
                    )

            except (ValueError, ConnectionError, NotImplementedError) as e:
                logging.warning(f"Attempt {attempt + 1}/{max_retries} failed: {str(e)}")
                if attempt == max_retries - 1:
                    logging.error("Max retries reached. Giving up.")
                    return None
                time.sleep(0.5)  # Wait before retrying

        return None  # This line should never be reached due to the return in the loop, but it's here for completeness

    def get_info(self, key):
        if packet := self._transceive(RequestCodeEnum.GET_INFO, bytes((key,)), key):
            match key:
                case InfoEnum.DEVICESERIAL:
                    return packet.data.hex()
                case InfoEnum.SOFTVERSION:
                    return _packet_to_int(packet) / 100
                case InfoEnum.HARDVERSION:
                    return _packet_to_int(packet) / 100
                case _:
                    return _packet_to_int(packet)
        else:
            return None

    def get_rfid(self):
        packet = self._transceive(RequestCodeEnum.GET_RFID, b"\x01")
        data = packet.data

        if data[0] == 0:
            return None
        uuid = data[0:8].hex()
        idx = 8

        barcode_len = data[idx]
        idx += 1
        barcode = data[idx : idx + barcode_len].decode()

        idx += barcode_len
        serial_len = data[idx]
        idx += 1
        serial = data[idx : idx + serial_len].decode()

        idx += serial_len
        total_len, used_len, type_ = struct.unpack(">HHB", data[idx:])
        return {
            "uuid": uuid,
            "barcode": barcode,
            "serial": serial,
            "used_len": used_len,
            "total_len": total_len,
            "type": type_,
        }

    def heartbeat(self):
        packet = self._transceive(RequestCodeEnum.HEARTBEAT, b"\x01")
        closingstate = None
        powerlevel = None
        paperstate = None
        rfidreadstate = None

        match len(packet.data):
            case 20:
                paperstate = packet.data[18]
                rfidreadstate = packet.data[19]
            case 13:
                closingstate = packet.data[9]
                powerlevel = packet.data[10]
                paperstate = packet.data[11]
                rfidreadstate = packet.data[12]
            case 19:
                closingstate = packet.data[15]
                powerlevel = packet.data[16]
                paperstate = packet.data[17]
                rfidreadstate = packet.data[18]
            case 10:
                closingstate = packet.data[8]
                powerlevel = packet.data[9]
                rfidreadstate = packet.data[8]
            case 9:
                closingstate = packet.data[8]

        return {
            "closingstate": closingstate,
            "powerlevel": powerlevel,
            "paperstate": paperstate,
            "rfidreadstate": rfidreadstate,
        }

    def set_label_type(self, n):
        assert 1 <= n <= 3
        packet = self._transceive(RequestCodeEnum.SET_LABEL_TYPE, bytes((n,)), 16)
        return bool(packet.data[0])

    def set_label_density(self, n):
        assert 1 <= n <= 5  # B21 has 5 levels, not sure for D11
        packet = self._transceive(RequestCodeEnum.SET_LABEL_DENSITY, bytes((n,)), 16)
        return bool(packet.data[0])

    def start_print(self, total_pages):
        assert (
            0 <= total_pages <= 65535
        )  # assume total_pages can not be greater than 65535 (2 bytes)

        command = struct.pack("H", total_pages)
        packet = self._transceive(
            RequestCodeEnum.START_PRINT, b"\x00" + command + b"\x00\x00\x00\x00"
        )
        return bool(packet.data[0])

    def end_print(self):
        packet = self._transceive(RequestCodeEnum.END_PRINT, b"\x01")
        return bool(packet.data[0])

    def start_page_print(self):
        packet = self._transceive(RequestCodeEnum.START_PAGE_PRINT, b"\x01")
        return bool(packet.data[0])

    def end_page_print(self):
        packet = self._transceive(RequestCodeEnum.END_PAGE_PRINT, b"\x01")
        return bool(packet.data[0])

    def allow_print_clear(self):
        packet = self._transceive(RequestCodeEnum.ALLOW_PRINT_CLEAR, b"\x01", 16)
        return bool(packet.data[0])

    def set_dimension(self, w, h, copies):
        packet = self._transceive(
            RequestCodeEnum.SET_DIMENSION, struct.pack(">HHH", w, h, copies)
        )
        return bool(packet.data[0])

    def set_quantity(self, n):
        packet = self._transceive(RequestCodeEnum.SET_QUANTITY, struct.pack(">H", n))
        return bool(packet.data[0])

    def get_print_status(self):
        packet = self._transceive(RequestCodeEnum.GET_PRINT_STATUS, b"\x01", 16)

        page, progress1, progress2 = struct.unpack(">HBB", packet.data)

        return {"page": page, "progress1": progress1, "progress2": progress2}
