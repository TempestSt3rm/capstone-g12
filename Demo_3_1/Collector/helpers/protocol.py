"""One batch command, then packets: uint8 LTF mask + uint32 ID + signed CSI bytes."""
from dataclasses import dataclass
from enum import IntEnum
import struct
import time

MARKER=b"CSI_DATA:"
HEADER=struct.Struct("<BI")
REQUEST=struct.Struct("<IIB")
SUMMARY=struct.Struct("<II")


class Message(IntEnum):
    HELLO=0x80
    WELCOME=0x81
    READY=0x82
    START=0x83
    ARMED=0x84
    PROBE=0x85
    DONE=0x86
    ERROR=0x87
    CANCEL=0x88


ERRORS={1:"INVALID_COMMAND",2:"RECEIVER_NOT_READY",3:"BUSY",4:"BATCH_TIMEOUT",
        5:"RECEIVER_DISCONNECTED",6:"INVALID_PACKET",7:"PACKET_ID_ERROR",
        8:"INVALID_CSI_LAYOUT",9:"WRITE_FAILED",10:"BATCH_BUFFER_ALLOCATION_FAILED",
        11:"RECEIVER_HEARTBEAT_TIMEOUT (no receiver traffic for 5 seconds)"}


def payload_size(code):
    if 1<=code<=7:
        return code.bit_count()*128
    sizes={Message.HELLO:0,Message.WELCOME:0,Message.READY:0,Message.START:9,
           Message.ARMED:9,Message.PROBE:128,Message.DONE:8,Message.ERROR:2,Message.CANCEL:0}
    if code not in sizes:
        raise ValueError(f"Invalid packet mask/control code: {code}")
    return sizes[code]


@dataclass(frozen=True)
class Frame:
    code:int
    packet_id:int=0
    payload:bytes=b""

    @property
    def is_sample(self):
        return 1<=self.code<=7

    def encode(self):
        if len(self.payload)!=payload_size(self.code):
            raise ValueError("Packet size does not match LTF mask/control code")
        return MARKER+HEADER.pack(self.code,self.packet_id)+self.payload


class FrameDecoder:
    def __init__(self):
        self.buffer=bytearray()

    def feed(self,data):
        self.buffer.extend(data)

    def pop(self):
        start=self.buffer.find(MARKER)
        if start<0:
            # A marker can span serial reads. Keep its possible trailing prefix.
            keep=len(MARKER)-1
            if len(self.buffer)>keep:
                del self.buffer[:-keep]
            return None
        del self.buffer[:start]
        header_start=len(MARKER)
        if len(self.buffer)<header_start+HEADER.size:
            return None
        code,packet_id=HEADER.unpack_from(self.buffer,header_start)
        size=payload_size(code)
        payload_start=header_start+HEADER.size
        end=payload_start+size
        if len(self.buffer)<end:
            return None
        result=Frame(code,packet_id,bytes(self.buffer[payload_start:end]))
        del self.buffer[:end]
        return result


class ReceiverClient:
    def __init__(self,transport,handshake_timeout=20,baud_rate=115200):
        self.transport=transport
        self.handshake_timeout=handshake_timeout
        self.baud_rate=baud_rate
        self.decoder=FrameDecoder()
        self.ready=False
        self.summary=None

    def _read_chunk(self):
        waiting=getattr(self.transport,"in_waiting",0)
        self.decoder.feed(self.transport.read(max(1,min(waiting,4096))))

    def read_line(self,deadline):
        while time.monotonic()<deadline:
            newline=self.decoder.buffer.find(b"\n")
            if newline>=0:
                line=bytes(self.decoder.buffer[:newline]).strip()
                del self.decoder.buffer[:newline+1]
                return line.decode("ascii",errors="replace")
            if len(self.decoder.buffer)>4096:
                raise ValueError("Oversize handshake response")
            self._read_chunk()
        raise TimeoutError("Transmitter/receiver handshake timed out")

    def read_frame(self,deadline):
        while time.monotonic()<deadline:
            frame=self.decoder.pop()
            if frame is not None:
                return frame
            self._read_chunk()
        raise TimeoutError("CSI batch timed out or ended with a partial packet")

    def _send(self,command):
        wire=(command+"\n").encode("ascii")
        if self.transport.write(wire)!=len(wire):
            raise OSError("Incomplete command write; reconnect before collecting")

    def start_session(self):
        deadline=time.monotonic()+self.handshake_timeout
        next_ready=0
        while time.monotonic()<deadline:
            if time.monotonic()>=next_ready:
                self._send("READY")
                next_ready=time.monotonic()+1
            try:
                line=self.read_line(min(deadline,next_ready))
            except TimeoutError:
                continue
            if line=="READY":
                self.ready=True
                print("Receiver handshake confirmed by transmitter.", flush=True)
                return
            if line.startswith("ERROR "):
                self._error(int(line.split()[1]))
        raise TimeoutError("Receiver handshake did not complete through the transmitter")

    @staticmethod
    def _error(code):
        raise RuntimeError(f"Transmitter/receiver error: {ERRORS.get(code,code)}")

    def batch_frames(self,count,mask,timeout_ms=120000):
        if not 1<=count<=100000 or not 1<=mask<=7 or not 100<=timeout_ms<=600000:
            raise ValueError("Invalid batch count, LTF mask, or timeout")
        if not self.ready:
            raise RuntimeError("Start a session before collecting")
        self._send(f"COLLECT {count} {mask} {timeout_ms}")
        # Acquisition finishes first, then the transmitter dumps its buffered
        # batch at the configured baud. Allow transfer time separately.
        dump_seconds=count*(len(MARKER)+HEADER.size+384)*10/self.baud_rate
        deadline=time.monotonic()+timeout_ms/1000+dump_seconds+5
        while True:
            line=self.read_line(deadline)
            if line.startswith("ERROR "):
                self._error(int(line.split()[1]))
            if line.startswith("BATCH "):
                fields=line.split()
                if len(fields)!=4:
                    raise ValueError("Invalid batch acknowledgment")
                first,accepted,actual_mask=map(int,fields[1:])
                if not first or first+count>0xffffffff or accepted!=count or actual_mask!=mask:
                    raise ValueError("Batch acknowledgment does not match request")
                print(f"Receiver armed: collecting {count} samples into transmitter memory.", flush=True)
                break
        received=0
        self.summary=None
        while True:
            frame=self.read_frame(deadline)
            if frame.packet_id!=first+received:
                raise ValueError("Missing, duplicate, or out-of-order packet ID")
            if frame.is_sample:
                if received>=count or frame.code & mask!=mask:
                    raise ValueError("Unexpected CSI mask or extra sample")
                if received==0:
                    print("Collection finished; receiving the buffered serial dump.", flush=True)
                received+=1
            elif frame.code==Message.ERROR:
                self._error(struct.unpack("<H",frame.payload)[0])
            elif frame.code==Message.DONE:
                completed,dropped=SUMMARY.unpack(frame.payload)
                if received!=count or completed!=count:
                    raise ValueError("Batch ended before requested sample count")
                self.summary=dict(count=completed,dropped=dropped)
                yield frame
                return
            else:
                raise ValueError("Unexpected packet in CSI stream")
            yield frame
