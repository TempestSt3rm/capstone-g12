"""Locate 64 imaginary/real pairs for each set bit in the CSI header mask."""
from dataclasses import dataclass
import struct

LTF_NAMES=("LLTF","HT-LTF","STBC-HT-LTF")


def columns_for_mask(mask):
    if type(mask) is not int or not 1<=mask<=7:
        raise ValueError("LTF mask must be 1..7")
    return [name for bit in range(3) if mask & (1<<bit)
            for index in range(bit*64,(bit+1)*64)
            for name in (f"img_{index}",f"real_{index}")]


@dataclass(frozen=True)
class Measurement:
    frame:object
    features:list[int]
    blocks:dict


class SampleValidator:
    def __init__(self,required_mask):
        columns_for_mask(required_mask)
        self.required_mask=required_mask

    def validate(self,frame):
        mask=frame.code
        if (not frame.is_sample or mask & self.required_mask!=self.required_mask
                or len(frame.payload)!=mask.bit_count()*128):
            raise ValueError("CSI size does not match the requested LTF mask")
        blocks={}
        offset=0
        features=[]
        for bit,name in enumerate(LTF_NAMES):
            if mask & (1<<bit):
                values=list(struct.unpack_from("<128b",frame.payload,offset))
                blocks[name]=values
                if self.required_mask & (1<<bit):
                    # The raw .bin preserves every byte. Always mask the first four
                    # LLTF bytes in CSV because hardware may report them invalid.
                    features.extend(([0]*4+values[4:]) if bit==0 else values)
                offset+=128
        return Measurement(frame,features,blocks)
