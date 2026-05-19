import json

with open("shaking.json") as f:
    data = json.load(f)

for pkt in data["packets"][:5]:
    rb = pkt["rest_bytes"]
    vals = []
    for i in range(4):
        b0, b1, b2 = rb[3*i], rb[3*i+1], rb[3*i+2]
        v_even = b0 | ((b1 & 0x0F) << 8)
        v_odd  = (b1 >> 4) | (b2 << 4)
        vals.extend([v_even, v_odd])
    # Last chunk (V8)
    b0, b1 = rb[12], rb[13]
    v8 = b0 | ((b1 & 0x0F) << 8)
    vals.append(v8)
    
    # Sign extend 12-bit
    signed = [v if v < 2048 else v - 4096 for v in vals]
    print("V0-V2:", signed[0:3])
    print("V3-V5:", signed[3:6])
    print("V6-V8:", signed[6:9])
    
