#!/usr/bin/env python3
# HP DeskJet 3700 USB LEDM -> local EWS gateway v10: enumerate only live WinUSB device interfaces
# Requires: Windows, Python 3.9+, USB connected, WinUSB driver on MI_02,
#           openssl.exe in PATH or python 'cryptography' package for local-only certificate.
# Binds ONLY 127.0.0.1; does not change printer firmware/settings by itself.
# Browser may send setting changes on button click: GET, POST, PUT etc are forwarded to USB.
# Press Ctrl+C to stop.  Do NOT expose this gateway beyond localhost.
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import gzip
import zlib
import http.server
import io
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

DEVICE_MATCH = 'VID_03F0&PID_0653&MI_02'
LOCK = threading.RLock()
MAX_RESPONSE = 16 * 1024 * 1024
READ_TIMEOUT = 12


def device_paths():
    """Enumerate *currently present* WinUSB interface symbolic links using
    Windows Configuration Manager (CM_...). Registry DeviceClasses entries may
    persist long after a USB device has been unplugged.

    The interface class GUID was observed on this HP MI_02 device. Search the
    registry only for additional GUID *classes*, never as live device paths.
    """
    import uuid
    import winreg

    interface_guids = {'afb4b8b5-6712-4af4-9c8e-127a201138d7'}
    regroot = r'SYSTEM\CurrentControlSet\Control\DeviceClasses'
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, regroot) as root:
            i = 0
            while True:
                try:
                    cls = winreg.EnumKey(root, i)
                    i += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(root, cls) as ck:
                        j = 0
                        while True:
                            try:
                                name = winreg.EnumKey(ck, j)
                                j += 1
                            except OSError:
                                break
                            if DEVICE_MATCH.lower() in name.lower():
                                interface_guids.add(cls.strip('{}').lower())
                except OSError:
                    continue
    except OSError as exc:
        print('[USB] Registry class GUID discovery unavailable: ' + str(exc), flush=True)

    cm = ctypes.WinDLL('cfgmgr32', use_last_error=True)
    cm.CM_Get_Device_Interface_List_SizeW.argtypes = [
        ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p, wintypes.LPCWSTR,
        wintypes.ULONG]
    cm.CM_Get_Device_Interface_List_SizeW.restype = wintypes.ULONG
    cm.CM_Get_Device_Interface_ListW.argtypes = [
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
        wintypes.ULONG, wintypes.ULONG]
    cm.CM_Get_Device_Interface_ListW.restype = wintypes.ULONG

    # CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0, currently active only.
    paths = []
    for guid_txt in sorted(interface_guids):
        try:
            guid_bytes = (ctypes.c_ubyte * 16).from_buffer_copy(
                uuid.UUID(guid_txt).bytes_le)
        except ValueError:
            continue
        class_guid = ctypes.cast(guid_bytes, ctypes.c_void_p)
        for attempt in range(3):
            size = wintypes.ULONG(0)
            rc = cm.CM_Get_Device_Interface_List_SizeW(
                ctypes.byref(size), class_guid, None, 0)
            if rc != 0:
                print(f'[USB] CM size query failed for {guid_txt}: CR={rc}', flush=True)
                break
            if size.value <= 1:
                break
            buf = ctypes.create_unicode_buffer(size.value)
            rc = cm.CM_Get_Device_Interface_ListW(
                class_guid, None, ctypes.cast(buf, ctypes.c_void_p),
                size.value, 0)
            if rc == 26:  # CR_BUFFER_SMALL, retry (may change on reconnect).
                continue
            if rc != 0:
                print(f'[USB] CM interface list failed for {guid_txt}: CR={rc}', flush=True)
                break
            items = buf[:].split('\x00')
            for path in items:
                if DEVICE_MATCH.lower() in path.lower() and path not in paths:
                    paths.append(path)
            break
    print(f'[USB] Active MI_02 WinUSB interface paths: {len(paths)}', flush=True)
    for path in paths:
        print('       ' + path, flush=True)
    return paths


class UsbInterfaceDescriptor(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('bLength', ctypes.c_ubyte), ('bDescriptorType', ctypes.c_ubyte),
        ('bInterfaceNumber', ctypes.c_ubyte), ('bAlternateSetting', ctypes.c_ubyte),
        ('bNumEndpoints', ctypes.c_ubyte), ('bInterfaceClass', ctypes.c_ubyte),
        ('bInterfaceSubClass', ctypes.c_ubyte), ('bInterfaceProtocol', ctypes.c_ubyte),
        ('iInterface', ctypes.c_ubyte),
    ]


class PipeInformation(ctypes.Structure):
    _fields_ = [
        ('PipeType', ctypes.c_int), ('PipeId', ctypes.c_ubyte),
        ('MaximumPacketSize', ctypes.c_ushort), ('Interval', ctypes.c_ubyte)
    ]


class WinUsbBridge:
    def __init__(self):
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.w = ctypes.WinDLL('winusb', use_last_error=True)
        self.k.CreateFileW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        self.k.CreateFileW.restype = wintypes.HANDLE
        self.k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k.CloseHandle.restype = wintypes.BOOL
        self.w.WinUsb_Initialize.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        self.w.WinUsb_Initialize.restype = wintypes.BOOL
        self.w.WinUsb_Free.argtypes = [ctypes.c_void_p]
        self.w.WinUsb_Free.restype = wintypes.BOOL
        self.w.WinUsb_QueryInterfaceSettings.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.POINTER(UsbInterfaceDescriptor)]
        self.w.WinUsb_QueryInterfaceSettings.restype = wintypes.BOOL
        self.w.WinUsb_QueryPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.POINTER(PipeInformation)]
        self.w.WinUsb_QueryPipe.restype = wintypes.BOOL
        self.w.WinUsb_SetPipePolicy.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, wintypes.ULONG, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        self.w.WinUsb_SetPipePolicy.restype = wintypes.BOOL
        self.w.WinUsb_WritePipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p]
        self.w.WinUsb_WritePipe.restype = wintypes.BOOL
        self.w.WinUsb_ReadPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p]
        self.w.WinUsb_ReadPipe.restype = wintypes.BOOL
        self.paths = device_paths()
        if not self.paths:
            raise RuntimeError('No ACTIVE HP WinUSB MI_02 path. Check USB cable and Get-PnpDevice -PresentOnly for VID_03F0&PID_0653&MI_02.')
        self.active_path = None
        for path in self.paths:
            try:
                status, hdrs, body = self._exchange(path, 'GET', '/DevMgmt/ProductConfigDyn.xml', [], b'', deadline=8)
                if status == 200:
                    self.active_path = path
                    break
            except Exception as e:
                print('[USB] Probe failed for ' + path + ': ' + str(e), flush=True)
        if not self.active_path:
            raise RuntimeError('Live HP MI_02 interface found, but read-only GET probe failed. Check whether another gateway still owns the USB interface.')
        print('[USB] Connected to MI_02 FF/04/01; GET /DevMgmt/ProductConfigDyn.xml -> 200 OK')

    def _exchange(self, path, method, resource, headers, body, deadline=READ_TIMEOUT):
        handle = self.k.CreateFileW(path, 0xC0000000, 3, None, 3, 0x40000000, None)
        invalid = ctypes.c_void_p(-1).value
        if handle is None or handle == invalid:
            raise OSError(ctypes.get_last_error(), 'CreateFileW failed')
        usb = ctypes.c_void_p()
        try:
            if not self.w.WinUsb_Initialize(handle, ctypes.byref(usb)):
                raise OSError(ctypes.get_last_error(), 'WinUsb_Initialize failed')
            desc = UsbInterfaceDescriptor()
            if not self.w.WinUsb_QueryInterfaceSettings(usb, 0, ctypes.byref(desc)):
                raise OSError(ctypes.get_last_error(), 'WinUsb_QueryInterfaceSettings failed')
            if (desc.bInterfaceClass, desc.bInterfaceSubClass, desc.bInterfaceProtocol) != (0xFF, 0x04, 0x01):
                raise RuntimeError('Unexpected USB interface; expected FF/04/01')
            ep_in = ep_out = None
            for i in range(desc.bNumEndpoints):
                pipe = PipeInformation()
                if not self.w.WinUsb_QueryPipe(usb, 0, i, ctypes.byref(pipe)):
                    raise OSError(ctypes.get_last_error(), 'WinUsb_QueryPipe failed')
                if pipe.PipeType == 2:
                    if pipe.PipeId & 0x80:
                        ep_in = pipe.PipeId
                    else:
                        ep_out = pipe.PipeId
            if ep_in is None or ep_out is None:
                raise RuntimeError('Missing bulk IN/OUT endpoints')
            timeout = wintypes.ULONG(10000)
            for ep in (ep_in, ep_out):
                if not self.w.WinUsb_SetPipePolicy(usb, ep, 3, ctypes.sizeof(timeout), ctypes.byref(timeout)):
                    raise OSError(ctypes.get_last_error(), 'WinUsb_SetPipePolicy failed')
            lines = [f'{method} {resource} HTTP/1.1', 'Host: localhost',
                     'User-Agent: hplip/3.0', 'Accept: */*', 'Accept-Encoding: identity', 'Connection: close']
            for hname, hvalue in headers:
                if hname.lower() in {'content-type', 'accept-language', 'authorization', 'cookie'}:
                    lines.append(f'{hname}: {hvalue}')
            lines.append(f'Content-Length: {len(body)}')
            packet = ('\r\n'.join(lines) + '\r\n\r\n').encode('latin-1') + body
            if len(packet) > 8 * 1024 * 1024:
                raise ValueError('Request exceeds 8 MiB safeguard')
            transferred = wintypes.ULONG(0)
            packet_buf = ctypes.create_string_buffer(packet)
            if not self.w.WinUsb_WritePipe(usb, ep_out, packet_buf, len(packet), ctypes.byref(transferred), None):
                raise OSError(ctypes.get_last_error(), 'WinUsb_WritePipe failed')
            if transferred.value != len(packet):
                raise RuntimeError(f'USB short write: {transferred.value}/{len(packet)}')
            data = bytearray()
            start = time.monotonic()
            no_body_since = None
            while time.monotonic() - start < deadline and len(data) < MAX_RESPONSE:
                buf = ctypes.create_string_buffer(8192)
                received = wintypes.ULONG(0)
                if not self.w.WinUsb_ReadPipe(usb, ep_in, buf, 8192, ctypes.byref(received), None):
                    raise OSError(ctypes.get_last_error(), 'WinUsb_ReadPipe failed')
                if received.value:
                    data.extend(buf.raw[:received.value])
                    no_body_since = None
                    if response_complete(data):
                        break
                else:
                    if no_body_since is None:
                        no_body_since = time.monotonic()
                    # No framing may mean a header-only OPTIONS / 301; allow short settle time.
                    if b'\r\n\r\n' in data and not has_response_length(data) and time.monotonic() - no_body_since > 0.45:
                        break
                    time.sleep(0.025)
            if not data:
                raise TimeoutError('No USB HTTP response')
            return decode_http_response(bytes(data))
        finally:
            if usb.value:
                self.w.WinUsb_Free(usb)
            self.k.CloseHandle(handle)

    def request(self, method, resource, headers, body):
        # WinUSB endpoint is a single ordered byte stream: don't allow concurrency.
        with LOCK:
            return self._exchange(self.active_path, method, resource, headers, body)


def has_response_length(data):
    head, _, _ = bytes(data).partition(b'\r\n\r\n')
    low = head.lower()
    return b'transfer-encoding: chunked' in low or b'content-length:' in low


def chunked_complete(body):
    pos = 0
    while True:
        newline = body.find(b'\r\n', pos)
        if newline < 0:
            return False
        rawsize = body[pos:newline].split(b';', 1)[0].strip()
        try:
            size = int(rawsize, 16)
        except ValueError:
            return False
        pos = newline + 2
        if size == 0:
            if body[pos:pos + 2] == b'\r\n':
                return True
            # For trailer header lines, a CRLFCRLF terminator ends them.
            return b'\r\n\r\n' in body[pos:]
        if len(body) < pos + size + 2:
            return False
        pos += size
        if body[pos:pos + 2] != b'\r\n':
            return False
        pos += 2


def response_complete(data):
    raw = bytes(data)
    if b'\r\n\r\n' not in raw:
        return False
    head, _, body = raw.partition(b'\r\n\r\n')
    hlow = head.lower()
    if b'transfer-encoding: chunked' in hlow:
        return chunked_complete(body)
    m = re.search(rb'(?im)^content-length:\s*(\d+)\s*$', head)
    if m:
        return len(body) >= int(m.group(1))
    return False


def dechunk(body):
    pos = 0
    parts = []
    while True:
        e = body.find(b'\r\n', pos)
        if e < 0:
            raise ValueError('Incomplete chunk length')
        size = int(body[pos:e].split(b';')[0], 16)
        pos = e + 2
        if size == 0:
            return b''.join(parts)
        if len(body) < pos + size:
            raise ValueError('Incomplete chunk payload')
        parts.append(body[pos:pos + size])
        pos += size + 2


def decode_http_response(data):
    head, sep, body = data.partition(b'\r\n\r\n')
    if not sep:
        raise ValueError('USB response has no HTTP header terminator')
    lines = head.decode('latin-1', errors='replace').split('\r\n')
    status = int(lines[0].split(' ')[1])
    headers = []
    for line in lines[1:]:
        if ':' in line:
            k, v = line.split(':', 1)
            headers.append((k.strip(), v.strip()))
    d = {k.lower(): v for k, v in headers}
    if 'chunked' in d.get('transfer-encoding', '').lower():
        body = dechunk(body)
    elif 'content-length' in d:
        body = body[:int(d['content-length'])]
    return status, headers, body


def decode_printer_encoding(headers, payload):
    """Decode HTTP Content-Encoding *before* stripping that header for the browser.

    Chunked transfer framing has already been removed by decode_http_response.
    Return (decoded_body, info) so gateway can serve a correct Content-Length.
    """
    values = [v for k, v in headers if k.lower() == 'content-encoding']
    encodings = [e.strip().lower() for e in ','.join(values).split(',') if e.strip()]
    body = payload
    info = []
    for enc in reversed(encodings):
        if enc == 'identity':
            continue
        original_len = len(body)
        try:
            if enc in ('gzip', 'x-gzip'):
                body = gzip.decompress(body)
            elif enc == 'deflate':
                try:
                    body = zlib.decompress(body)
                except zlib.error:
                    body = zlib.decompress(body, -zlib.MAX_WBITS)
            elif enc == 'br':
                try:
                    import brotli
                except ImportError:
                    raise RuntimeError('Printer sent Brotli content; install decoder: py -3 -m pip install brotli')
                body = brotli.decompress(body)
            else:
                raise RuntimeError('Unsupported printer Content-Encoding: ' + enc)
        except (OSError, EOFError, zlib.error, ValueError) as exc:
            raise RuntimeError('Could not decode ' + enc + ' printer response (' +
                               body[:16].hex(' ') + '): ' + str(exc)) from exc
        if len(body) > MAX_RESPONSE:
            raise RuntimeError('Decompressed response exceeded size limit')
        info.append(f'{enc} {original_len}->{len(body)} bytes')
    # Some older HP devices send a compressed body without declaring its encoding.
    if not encodings and body.startswith(b'\x1f\x8b'):
        original_len = len(body)
        body = gzip.decompress(body)
        info.append(f'gzip detected by magic {original_len}->{len(body)} bytes')
    elif not encodings and len(body) >= 2 and body[0] == 0x78 and ((body[0] << 8) + body[1]) % 31 == 0:
        try:
            plain = zlib.decompress(body)
            if len(plain) <= MAX_RESPONSE:
                info.append(f'zlib detected by magic {len(body)}->{len(plain)} bytes')
                body = plain
        except zlib.error:
            pass
    return body, ', '.join(info) or 'identity'


def create_certificate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    cert = directory / 'localhost-cert.pem'
    key = directory / 'localhost-key.pem'
    # Local ephemeral self-signed certificate for browser -> PC only;
    # it does not modify the HP printer's certificate.
    if cert.exists() and key.exists():
        return str(cert), str(key)
    cmd = shutil.which('openssl')
    if cmd:
        subprocess.run([cmd, 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-nodes',
            '-keyout', str(key), '-out', str(cert), '-days', '365',
            '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return str(cert), str(key)
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from datetime import datetime, timedelta, timezone
        from ipaddress import ip_address
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'localhost')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ip_address('127.0.0.1'))]), critical=False)
            .sign(private_key, hashes.SHA256()))
        key.write_bytes(private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        return str(cert), str(key)
    except ImportError:
        raise RuntimeError('Certificate creation needs openssl.exe in PATH, or: py -m pip install cryptography')


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'HP-USB-EWS-Gateway/1.0'
    sys_version = ''
    def log_message(self, fmt, *args):
        print('[LOCAL] %s - %s' % (self.address_string(), fmt % args), flush=True)
    def do_GET(self): self.forward()
    def do_HEAD(self): self.forward()
    def do_OPTIONS(self): self.forward()
    def do_POST(self): self.forward()
    def do_PUT(self): self.forward()
    def do_DELETE(self): self.forward()

    def forward(self):
        global BRIDGE, PORT, PRINTER_HOSTNAME, PRINTER_IP, SCHEME
        try:
            route = urllib.parse.urlsplit(self.path)
            if route.scheme or route.netloc:
                raise ValueError('Absolute URL not accepted')
            path = (route.path or '/') + (('?' + route.query) if route.query else '')
            # URL paths are passed to HP exactly; fragment identifiers never reach server.
            if len(path) > 8192:
                raise ValueError('Request path too long')
            length = int(self.headers.get('Content-Length', 0))
            if length < 0 or length > 8 * 1024 * 1024:
                raise ValueError('Request body too large')
            body = self.rfile.read(length) if length else b''
            # Browser cache validators must not reach HP. The device otherwise
            # replies 304 with no body, leaving pre-v8 corrupt bytes cached.
            upstream_headers = [(n, v) for n, v in self.headers.items()
                                if n.lower() not in ('if-none-match', 'if-modified-since',
                                                     'if-match', 'if-unmodified-since',
                                                     'if-range', 'range')]
            status, headers, payload = BRIDGE.request(self.command, path, upstream_headers, body)
            if status == 304 and self.command in ('GET', 'HEAD'):
                # A 304 to an unconditional request is unusable at this proxy.
                # Retry once with a query cache-buster so the browser gets bytes.
                sep = '&' if '?' in path else '?'
                retry_path = path + sep + '_hp_usb_nocache=' + str(int(time.time() * 1000))
                print('[CACHE] Printer returned 304 without validators; retrying ' + retry_path, flush=True)
                status, headers, payload = BRIDGE.request(self.command, retry_path, upstream_headers, body)
                if status == 304:
                    raise RuntimeError('Printer returned 304 again even for uncached request')
            # HP sends gzip/deflate even on some minimal HTTP responses. v7
            # dropped Content-Encoding without decoding, corrupting the EWS page.
            payload, coding = decode_printer_encoding(headers, payload)
            out_headers = {}
            for name, value in headers:
                key = name.lower()
                if key in ('connection', 'transfer-encoding', 'content-length', 'server', 'date', 'keep-alive', 'content-security-policy', 'content-security-policy-report-only', 'strict-transport-security', 'content-encoding', 'etag', 'last-modified', 'expires', 'cache-control', 'pragma', 'vary'):
                    continue
                if key == 'location':
                    if PRINTER_IP:
                        value = value.replace(f'http://{PRINTER_IP}', f'{SCHEME}://127.0.0.1:{PORT}')
                        value = value.replace(f'https://{PRINTER_IP}', f'{SCHEME}://127.0.0.1:{PORT}')
                    if PRINTER_HOSTNAME:
                        value = value.replace(f'http://{PRINTER_HOSTNAME}', f'{SCHEME}://127.0.0.1:{PORT}')
                        value = value.replace(f'https://{PRINTER_HOSTNAME}', f'{SCHEME}://127.0.0.1:{PORT}')
                    value = value.replace('http://localhost', f'{SCHEME}://127.0.0.1:{PORT}')
                    value = value.replace('https://localhost', f'{SCHEME}://127.0.0.1:{PORT}')
                out_headers[name] = value
            content_type = next((v for n, v in headers if n.lower() == 'content-type'), '')
            # Only rewrite absolute printer network URLs in textual assets.
            if any(term in content_type.lower() for term in ('text/', 'xml', 'javascript', 'json')):
                printer_origins = []
                if PRINTER_IP:
                    printer_origins.extend((f'https://{PRINTER_IP}', f'http://{PRINTER_IP}'))
                if PRINTER_HOSTNAME:
                    printer_origins.extend((f'https://{PRINTER_HOSTNAME}',
                                            f'http://{PRINTER_HOSTNAME}'))
                for old in printer_origins:
                    payload = payload.replace(old.encode('ascii'), f'{SCHEME}://127.0.0.1:{PORT}'.encode('ascii'))
            self.send_response(status)
            for name, value in out_headers.items():
                if name.lower() != 'host':
                    self.send_header(name, value)
            # Never let browsers reuse corrupt v6/v7 cached page bytes.
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Connection', 'close')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)
            self.close_connection = True
            print(f'[USB] {self.command} {path} -> {status}, {len(payload)} bytes, coding: {coding}', flush=True)
        except Exception as e:
            msg = (str(e) + '\n').encode('utf-8', errors='replace')
            print('[ERROR] ' + repr(e), flush=True)
            try:
                self.send_response(502)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(msg)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(msg)
                self.close_connection = True
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass


BRIDGE = None
PRINTER_IP = ''
PRINTER_HOSTNAME = ''
PORT = 8765
SCHEME = 'https'

def probe_local_server(port, scheme):
    """Direct TCP test. No OS/Firefox proxy is involved."""
    context = ssl._create_unverified_context() if scheme == 'https' else None
    with socket.create_connection(('127.0.0.1', port), timeout=5) as conn:
        conn.settimeout(20)
        if context:
            with context.wrap_socket(conn, server_hostname='localhost') as tls:
                negotiated = tls.version()
                tls.sendall(b'GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
                response = tls.recv(1024)
                status = response.split(b'\r\n', 1)[0].decode('latin-1', 'replace')
                print(f'[SELFTEST] TLS handshake OK ({negotiated}); {status}', flush=True)
                return
        conn.sendall(b'GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
        response = conn.recv(1024)
        status = response.split(b'\r\n', 1)[0].decode('latin-1', 'replace')
        print(f'[SELFTEST] Plain HTTP reachable; {status}', flush=True)


def main():
    global BRIDGE, PRINTER_HOSTNAME, PRINTER_IP, PORT, SCHEME
    p = argparse.ArgumentParser(description='Local EWS gateway for HP DeskJet 3700 via USB LEDM')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--printer-ip', default='', help='Optional printer IP used only to rewrite absolute URLs')
    p.add_argument('--printer-hostname', default='', help='Optional printer hostname used only to rewrite absolute URLs')
    p.add_argument('--cert-dir', default=str(Path.home() / 'HP_USB_EWS_Gateway'))
    p.add_argument('--http', action='store_true', help='Diagnostic fallback: serve plain HTTP instead of HTTPS on localhost')
    args = p.parse_args()
    if os.name != 'nt':
        raise SystemExit('This gateway requires Windows WinUSB.')
    PORT = args.port
    PRINTER_IP = args.printer_ip
    PRINTER_HOSTNAME = args.printer_hostname
    SCHEME = 'http' if args.http else 'https'
    print('HP DeskJet 3700 - USB EWS Gateway v10 (live USB paths)')
    print('USB required; printer HTTPS is never used. Ctrl+C to stop.')
    BRIDGE = WinUsbBridge()
    try:
        status, headers, payload = BRIDGE.request('GET', '/', [], b'')
        plain, coding = decode_printer_encoding(headers, payload)
        print(f'[USB] GET / -> HTTP {status}, raw={len(payload)} bytes, decoded={len(plain)} bytes, coding={coding}; Content-Type: ' +
            next((v for n, v in headers if n.lower() == 'content-type'), 'unknown'))
        print('[USB] Body preview (first 100 chars): ' + repr(plain[:100]))
    except Exception as e:
        print('[WARN] USB EWS root GET failed: ' + str(e))
    if args.http:
        print('[WARN] --http is for troubleshooting. Some EWS settings may demand an HTTPS browser origin.')
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), ProxyHandler)
    if not args.http:
        cert_path, key_path = create_certificate(Path(args.cert_dir))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert_path, key_path)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        print('[TLS] Python HTTPS socket enabled; local certificate loaded.')
    server_thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': 0.25}, daemon=True)
    server_thread.start()
    try:
        # Important: validate that the HTTPS port actually speaks TLS, bypassing Firefox/proxies.
        # This does a read-only USB GET, and does not change printer configuration.
        probe_local_server(PORT, SCHEME)
        print(f'OPEN IN FIREFOX: {SCHEME}://127.0.0.1:{PORT}/')
        print('Bound to 127.0.0.1 only; not available from other computers.')
        if not args.http:
            print('If Firefox fails while SELFTEST says TLS OK, check Firefox proxy settings.')
            print(f'Windows direct check: curl.exe -vk --noproxy "*" https://127.0.0.1:{PORT}/')
        print('Press Ctrl+C to stop.')
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print('\nStopped by user.')
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == '__main__':
    main()
