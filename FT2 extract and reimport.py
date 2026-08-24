from sys import argv
from pathlib import Path
from struct import pack, unpack, error as structerror
import json
import traceback
from io import BytesIO

try:
    font_file = Path(argv[1])
except:
    input("Drag an FT2 to the .py file.")
    exit()
else:
    json_file=font_file.with_suffix(".json")
    DDS_file=font_file.with_suffix(".dds")

def parse_FT2(FT2_bytes:bytes, endianness:str):

    FT2_bytesIO = BytesIO(FT2_bytes)
    
    filelen_raw = FT2_bytesIO.read(4)
    FT2_bytesIO.read(4) # whatever this 00 00 00 01 is

    # version specific variables
    maxDescent=None
    uses_SDF_texture=None
    distfieldpad=None
    distfieldcenter=None
    distfieldscale=None
    icgap=None
    hasExtraPerCharacterData=None
    singleChannelTexture=None
    
    endianness_le_be = "be" if endianness==">" else "le"

    filelen, = unpack(endianness+'I',filelen_raw)

    FT2_bytesIO.read(4) #TNFN fourcc, aleady checked for in detect_font_format

    version, = unpack(endianness+'I',FT2_bytesIO.read(4)) # FT2 format version

    print(f"Version: FT2 {version:02X} {endianness_le_be.upper()}")

    if version > 4:
        hasExtraPerCharacterData, = unpack('?',FT2_bytesIO.read(1))
    if version > 9:
        hasEmbeddedTexture, = unpack('?',FT2_bytesIO.read(1)) # embedded .texture file. seemingly for pre-tss games too?
    if version > 4:
        if version < 11:
            maxDescent, = unpack(endianness+'I',FT2_bytesIO.read(4))
        else:
            maxDescent, = unpack(endianness+'f',FT2_bytesIO.read(4))

    FT2_bytesIO.read(4) # font version (revision), i've never seen it stored.

    if version < 6:
        flags, = unpack(endianness+'H',FT2_bytesIO.read(2))
        if version == 4:
            uses_SDF_texture = bool(flags & (1<<2)) # literally the only flag, and only in version 04, and only in LCU Remaster (bruh)

    if version < 12:
        FT2_bytesIO.read(4) # useless "size" value, close to filelen in old FT2, null in new FT2

    if version < 8:
        header_posTable_size, header_charTable_size = (
            unpack(endianness+'II',FT2_bytesIO.read(8))
        )

    global_height, baseline, space_width = unpack(endianness+'fff',FT2_bytesIO.read(12))
    calculated_maxdescent = global_height - baseline
    if not hasExtraPerCharacterData:
        maxDescent = calculated_maxdescent

    if version < 4:
        FT2_bytesIO.read(4) # sendId, seems useless

    if 3 < version < 6:
        distfieldpad, = unpack(endianness+'f',FT2_bytesIO.read(4))

    # icgap doesn't work for games that use FONT_CONFIG.XML but is still present in every version.
    icgap, = unpack(endianness+'f',FT2_bytesIO.read(4))

    if version == 4:
        distfieldcenter, distfieldscale = unpack(endianness+'If',FT2_bytesIO.read(8))

    if version > 5:
        uses_SDF_texture, = unpack('?',FT2_bytesIO.read(1))
    
    if version > 2:
        FT2_bytesIO.read(4) # ROTV fourcc

    posTable_size, = unpack(endianness+'I',FT2_bytesIO.read(4))
    if version < 12 and header_posTable_size != posTable_size:
        print("Inconsistent PosTable size!")
        raise

    posTable = []

    attribute_names = ("x","y","width","height","baseline_offset","draw_offset","advance","page")
    attributes_count = 7 if version > 4 else 3
    # attributes_count = 8 if version == 7 else attributes_count

    for i in range(posTable_size):
        current_pos = {}
        for j in range(attributes_count):
            current_pos[attribute_names[j]], = unpack(endianness+'f',FT2_bytesIO.read(4))

        if version == 7:
            current_pos["page"], = unpack(endianness+'B',FT2_bytesIO.read(1))

        current_pos_new = current_pos
        try:
            current_pos["baseline_offset"]-=current_pos["height"]
            #calculate traditional height and use that in json output so it's easily importable into other FT2/FNT/QFN/etc versions
            if hasExtraPerCharacterData:
                # y + height + baseline_offset + max_descent - traditional_y = global_height
                # y + height + baseline_offset + max_descent - global_height = traditional_y
                traditional_y = (
                    + current_pos["y"]
                    + current_pos["height"]
                    + current_pos["baseline_offset"]
                    + calculated_maxdescent
                    - global_height
                )

                top_crop = global_height - current_pos["height"]
                bottom_crop = current_pos["baseline_offset"] + calculated_maxdescent
                current_pos["advance"]-=current_pos["width"]

            current_pos_new = {
                "x":current_pos["x"],
                "y":current_pos["y"],
                "width":current_pos["width"],
                "top_crop":0,
                "bottom_crop":0,
                "draw_offset":current_pos["draw_offset"],
                "advance":current_pos["advance"]
            }

            if hasExtraPerCharacterData:
                current_pos_new["top_crop"]=top_crop
                current_pos_new["bottom_crop"]=bottom_crop
                current_pos_new["y"]=traditional_y
            else:
                del current_pos_new["top_crop"]
                del current_pos_new["bottom_crop"]

            current_pos_new["page"] = current_pos["page"] #doing it here so except block only prevents this and not the other values
            
        except KeyError:
            pass
            
        posTable.append(current_pos_new)

    if version > 2:
        FT2_bytesIO.read(4) # ROTV fourcc

    charTable_size, = unpack(endianness+'I',FT2_bytesIO.read(4))
    if version < 12 and header_charTable_size != charTable_size:
        print("Inconsistent CharTable size!")
        raise

    chars = []

    for i in range(charTable_size):
        codepoint = FT2_bytesIO.read(2)
        pos_index, = unpack(endianness+'H',FT2_bytesIO.read(2))
        char = codepoint.decode("utf-16-"+endianness_le_be)
        if pos_index == 0x20:
            chars.append({"char":char,"width":space_width,"special":"space"})
            continue
            # space works weirdly, any character with pos_index == 0x20 is considered a space and its width is gotten from the header.
        if pos_index == 0x00:
            chars.append({"char":char,"width":space_width,"special":"string_ending"})
            continue
            # probably unintended feature but every character with pos_index == 0x00 is considered a string ending and anything coming after it in text.csv is ignored.
        try:
            chars.append({"char":char,**posTable[pos_index]})
        except IndexError:
            continue

    if version > 1:

        kerning = []

        if version > 2:
            FT2_bytesIO.read(4) # ROTV fourcc

        kernTable_size, = unpack(endianness+'I',FT2_bytesIO.read(4))

        for i in range(kernTable_size):
            charA = FT2_bytesIO.read(2)
            charB = FT2_bytesIO.read(2)
            gap, = unpack(endianness+'f',FT2_bytesIO.read(4))
            kerning.append((
                charA.decode("utf-16-"+endianness_le_be)+
                charB.decode("utf-16-"+endianness_le_be),
                gap)
            )

    if version > 12:
        FT2_bytesIO.read(4) #garbage metadata

    if version > 13:
        singleChannelTexture = bool(FT2_bytesIO.read(1))

    DDS_bytes = FT2_bytes[filelen+4:]
    
    MISC_ATTR_NAMES = {
        "global_height":global_height,
        "baseline":baseline,
        "tracking":icgap,
        "SDF_texture":uses_SDF_texture,
        "SDF_pad":distfieldpad,
        "SDF_center":distfieldcenter,
        "SDF_scale":distfieldscale,
        "max_descent":maxDescent,
        "height_cropping":hasExtraPerCharacterData, # all other differences besides this are handled by extractor/reimporter
        "single_channel_texture":singleChannelTexture
    }

    global used_attribute_names
    used_attribute_names = {key:value for key,value in MISC_ATTR_NAMES.items() if value is not None}

    json_ready = {
        "chars":chars,
        **used_attribute_names
    }

    if version > 1:
        json_ready["kerning"] = kerning

    return(json_ready,DDS_bytes)

def parse_lsw1_FNT(FNT_bytes:bytes, endianness:str):

    FNT_bytesIO = BytesIO(FNT_bytes)

    if DDS_file.exists():
        with open(DDS_file, mode='rb') as DDS_buffer:
            DDS_buffer.read(12)
            texture_height,texture_width = unpack('<II',DDS_buffer.read(8))
    else:
        print("DDS not found.")
        texture_height = input("Enter texture height: ")
        texture_width = input("Enter texture width: ")

    endianness_le_be = "be" if endianness==">" else "le"
    FNT_bytesIO.read(8)
    version, = unpack(endianness+'I',FNT_bytesIO.read(4))
    print(f"Version: LSW1 FNT {version:02X} {endianness_le_be.upper()}")

    global_height, = unpack(endianness+'H',FNT_bytesIO.read(2))
    FNT_bytesIO.read(2) #idk
    FNT_bytesIO.read(4) #idk
    baseline, = unpack(endianness+'H',FNT_bytesIO.read(2))
    tracking, = unpack(endianness+'H',FNT_bytesIO.read(2))
    FNT_bytesIO.read(4) #idk, probably useless garbage data
    postable_len, = unpack(endianness+'I',FNT_bytesIO.read(4))

    postable_values = []
    chars = []

    chartable_bytesIO = BytesIO(FNT_bytesIO.read(256)) # parse it after postable

    for i in range(postable_len):
        x,y,x_end = unpack(endianness+'fff',FNT_bytesIO.read(12))
        FNT_bytesIO.read(8)
        postable_values.append({
            "x":x*texture_width,
            "y":y*texture_width,
            "width":x_end*texture_width - x*texture_width
        })

    skip_byte = FNT_bytes[0x20] #00 character's index. only reason i'm not hardcoding it to 0xFF is because a weird LSW1 FNT variant in bionicle heroes has it as 0x00 instead?

    for codepoint in range(256):
        postable_index, = chartable_bytesIO.read(1)
        if postable_index == skip_byte:
            continue
        if codepoint == 0x20: # Space is exe hardcoded
            chars.append({"char":" "})
        chars.append({
            "char":chr(codepoint),
            **postable_values[postable_index]
        })

    global used_attribute_names
    used_attribute_names = {
        "global_height":global_height,
        "baseline":baseline,
        "tracking":tracking
    }

    json_ready = {
        "chars":chars,
        **used_attribute_names
    }

    return json_ready, None

def parse_TCS_QFN(QFN_bytes:bytes,endianness:str):

    QFN_bytesIO = BytesIO(QFN_bytes)

    endianness_le_be = "be" if endianness==">" else "le"

    QFN_bytesIO.read(8)
    QFN_bytesIO.read(4) # filesize, not needed for parsing
    poscount, charcount = unpack(endianness+"II",QFN_bytesIO.read(8))
    global_height, baseline, space_width\
        = unpack(endianness+"fff",QFN_bytesIO.read(12))
    QFN_bytesIO.read(4) # unk
    QFN_bytesIO.read(4) # unk
    QFN_bytesIO.read(4) # unk
    QFN_bytesIO.read(4) # unk
    QFN_bytesIO.read(4) # unk
    postable_offset, chartable_offset\
        = unpack(endianness+"II",QFN_bytesIO.read(8))
    QFN_bytesIO.read(4) # unk
    QFN_bytesIO.read(4) # unk
    QFN_bytesIO.read(4) # unk
    QFN_bytesIO.read(4) # unk

    postable_bytesIO = BytesIO(
        QFN_bytes[
            postable_offset:
            postable_offset + poscount*12
        ]
    )

    postable_values = []

    for i in range(poscount):
        x, y, width\
            = unpack(endianness+"fff", postable_bytesIO.read(12))
        postable_values.append({
            "x":x,
            "y":y,
            "width":width
        })

    chartable_bytesIO = BytesIO(
        QFN_bytes[
            chartable_offset:
            chartable_offset + charcount*4
        ]
    )

    chars = []

    for i in range(charcount):
        codepoint = chartable_bytesIO.read(2)
        pos_index, = unpack(endianness+'H',chartable_bytesIO.read(2))
        char = codepoint.decode("utf-16-"+endianness_le_be)
        if pos_index == 0x20:
            chars.append({"char":char,"width":space_width,"special":"space"})
            continue
            # space works weirdly, any character with pos_index == 0x20 is considered a space and its width is gotten from the header.
        if pos_index == 0x00:
            chars.append({"char":char,"width":space_width,"special":"string_ending"})
            continue
            # probably unintended feature but every character with pos_index == 0x00 is considered a string ending and it and anything coming after it in text.csv is ignored.
        try:
            chars.append({"char":char,**postable_values[pos_index]})
        except IndexError:
            continue

    global used_attribute_names
    used_attribute_names = {
        "global_height":global_height,
        "baseline":baseline
    }

    json_ready = {
        "chars":chars,
        **used_attribute_names
    }

    return json_ready, None
    
def parse_lsw2_FNT(FNT_bytes:bytes, endianness:str):

    FNT_bytesIO = BytesIO(FNT_bytes)

    endianness_le_be = "be" if endianness==">" else "le"
    reloctable_offset, = unpack(endianness+'i',FNT_bytesIO.read(4)) # relocation table offset, right after dds usually
    container_start = unpack(endianness+'i',FNT_bytesIO.read(4))[0] + 4 # + 4 because offsets are relative
    DDS_offset = unpack(endianness+'i',FNT_bytesIO.read(4))[0] + 8
    FNT_bytesIO = BytesIO(FNT_bytes[container_start:])

    FNT_bytesIO.read(4) #unk
    FNT_bytesIO.read(2) #unk
    FNT_bytesIO.read(2) #unk
    FNT_bytesIO.read(4) #unk
    poscount, charcount \
        = unpack(endianness+'II',FNT_bytesIO.read(8))
    global_height, baseline, space_width \
        = unpack(endianness+"fff",FNT_bytesIO.read(12))
    FNT_bytesIO.read(4) #unk
    tracking, = unpack(endianness+'f',FNT_bytesIO.read(4))
    FNT_bytesIO.read(4) #unk
    FNT_bytesIO.read(4) #unk "x"?
    FNT_bytesIO.read(4) #unk "y"?
    postable_offset \
        = container_start + 0x34 + unpack(endianness+"i",FNT_bytesIO.read(4))[0]
    chartable_offset \
        = container_start + 0x38 + unpack(endianness+"i",FNT_bytesIO.read(4))[0]
    FNT_bytesIO.read(4) #unk; runtime
    FNT_bytesIO.read(4) #unk; runtime
    FNT_bytesIO.read(4) #unk; runtime
    FNT_bytesIO.read(4) #unk; runtime

    postable_bytesIO = BytesIO(
        FNT_bytes[
            postable_offset:
            postable_offset + poscount*12
        ]
    )

    postable_values = []

    for i in range(poscount):
        x, y, width\
            = unpack(endianness+"fff", postable_bytesIO.read(12))
        postable_values.append({
            "x":x,
            "y":y,
            "width":width
        })

    chartable_bytesIO = BytesIO(
        FNT_bytes[
            chartable_offset:
            chartable_offset + charcount*4
        ]
    )

    chars = []

    for i in range(charcount):
        codepoint = chartable_bytesIO.read(2)
        pos_index, = unpack(endianness+'H',chartable_bytesIO.read(2))
        char = codepoint.decode("utf-16-"+endianness_le_be)
        if pos_index == 0x20:
            chars.append({"char":char,"width":space_width,"special":"space"})
            continue
            # space works weirdly, any character with pos_index == 0x20 is considered a space and its width is gotten from the header.
        if pos_index == 0x00:
            chars.append({"char":char,"width":space_width,"special":"string_ending"})
            continue
            # probably unintended feature but every character with pos_index == 0x00 is considered a string ending and it and anything coming after it in text.csv is ignored.
        try:
            chars.append({"char":char,**postable_values[pos_index]})
        except IndexError:
            continue

    global used_attribute_names
    used_attribute_names = {
        "global_height":global_height,
        "baseline":baseline,
        "tracking":tracking
    }

    json_ready = {
        "chars":chars,
        **used_attribute_names
    }

    # hacky solution; reloctable doesn't *need* to be exactly right after dds.
    # but it is in all the vanilla files i found and i don't wanna calculate the real dds length from the header because lazy :)
    DDS_bytes = FNT_bytes[DDS_offset:reloctable_offset]

    return json_ready, DDS_bytes

def generate_postable_chartable(chars_list:list[dict]) -> tuple[list[dict],list[tuple[str,int]]]:
    """return postable, chartable"""

    chars_list.sort(key = lambda x: x["char"]) #alphabetic order

    chars_dict = { #also removes duplicates
        dic["char"]: {a:b for a,b in dic.items() if a != "char"} 
        for dic in chars_list
    }

    reserved_special = {"string_ending":0x00, "space":0x20}
    reserved_alphabet = "!%,.0123456789?ABCDEFGHIJKLMNOPQRSTUVWXYZ[]abcdefghijklmnopqrstuvwxyz" # excluding space, that's special

    postable = [
        info for char,info in chars_dict.items() if "special" not in info
        and char not in reserved_alphabet
    ]

    for special_kw, special_idx in reserved_special.items():
        # just to make sure that index actually exists
        while len(postable) < special_idx: 
            postable.append({"x":0, "y":0, "width":0}) 

        postable.insert(special_idx, {"x":0, "y":0, "width":0})

    for char in reserved_alphabet:
        idx = ord(char)
        while len(postable) < ord(char): 
            postable.append({"x":0, "y":0, "width":0}) 

        postable.insert(idx, chars_dict[char])

    chartable = []

    idx=0
    c=0
    for char, info in chars_dict.items():

        idx=c
        if "special" in info and info["special"] in reserved_special:
            idx = reserved_special[info["special"]]
        elif char in reserved_alphabet:
            idx = ord(char)
        else:
            c+=1
            for special_idx in reserved_special.values():
                if idx >= special_idx:
                    idx+=1
            for reserved_char in reserved_alphabet:
                reserved_idx=ord(reserved_char)
                if idx >= reserved_idx:
                    idx+=1    

        chartable.append((char, idx))

    return postable, chartable

def reimport_FT2_replace_dds (FT2_bytes,dds_bytes,endianness):

    filelen = unpack(endianness+'I', FT2_bytes[:4])[0] + 4

    return FT2_bytes[:filelen] + dds_bytes



def reimport_FT2(
    FT2_bytes:bytes,
    endianness:str,
    json_info:dict,
    dds_bytes:bytes
):
    FT2_bytearray = bytearray(FT2_bytes)
    endianness_le_be = "be" if endianness==">" else "le"

    if json_info is None:
        return reimport_FT2_replace_dds(FT2_bytes, dds_bytes, endianness)
        

    # get header info from json first
    MISC_ATTR_NAMES = (
        "global_height",
        "baseline",
        "tracking",
        "SDF_texture",
        "SDF_pad",
        "SDF_center",
        "SDF_scale",
        "max_descent",
        "height_cropping", # all other differences besides this are handled by extractor/reimporter
        "single_channel_texture"
    )

    misc_attrs = []
    for attr_name in MISC_ATTR_NAMES:
        try:
            misc_attrs.append(json_info[attr_name])
        except KeyError:
            misc_attrs.append(None)

    global_height,\
    baseline,\
    icgap,\
    uses_SDF_texture,\
    distfieldpad,\
    distfieldcenter,\
    distfieldscale,\
    maxDescent,\
    hasExtraPerCharacterData,\
    singleChannelTexture = misc_attrs

    int_falsy = lambda a : int(a) if a else 0

    c = 0

    filelen_offset = c; c += 4
    c += 4 # dunno what the fuck this is
    c += 4 # TNFN or NFNT
    version, = unpack(endianness+'I',FT2_bytes[c:c+4]); c += 4

    if version > 4:
        FT2_bytearray[c] = int_falsy(hasExtraPerCharacterData); c += 1

    if version > 9:
        c+=1 # man fuck embedded .texture i don't care

    if version > 4:
        maxDescent_offset = c; c+=4 # written later in case it needs calculating using original FT2 baseline

    c+=4 # font revision, useless, dunno if i should put a watermark (such as RFWQ) there
    flags = bytearray(pack(endianness+"H",
        0x4 if version == 4 and uses_SDF_texture else 0
    ))

    if version < 6:
        FT2_bytearray[c:c+2] = flags; c+=2

    if version < 12:
        c+=4 #useless

    posTable_size0_offset = charTable_size0_offset = None

    if version < 8:
        posTable_size0_offset = c; c+=4
        charTable_size0_offset = c; c+=4

    FT2_bytearray[c:c+4] = pack(endianness+'f',global_height); c+=4

    if baseline is not None:
        FT2_bytearray[c:c+4] = pack(endianness+'f',baseline)
    else:
        baseline_from_FT2, = unpack(endianness+'f',FT2_bytes[c:c+4])
    c+=4
    
    try:
        FT2_bytearray[c:c+4] = pack(
            endianness+'f',
            next(i for i in json_info["chars"] if i["special"]=="space")["width"]
        )
    except KeyError:
        pass
    c+=4

    # write maxDescent now
    if version > 4:
        if hasExtraPerCharacterData:
            if maxDescent is not None:
                maxDescent_to_write = maxDescent
            elif baseline is None:
                maxDescent_to_write = global_height - baseline_from_FT2
            else:
                maxDescent_to_write = global_height - baseline
        else:
            maxDescent_to_write = 0

        if version < 11:
            maxDescent_to_write = pack(endianness+'I',maxDescent_to_write)
        else:
            maxDescent_to_write = pack(endianness+'f',maxDescent_to_write)

        FT2_bytearray[maxDescent_offset:maxDescent_offset+4] = (
            maxDescent_to_write
        )

    # calculated masDescent, different from stored maxDescent and used
    if baseline is None:
        calculated_maxdescent = global_height - baseline_from_FT2
    else:
        calculated_maxdescent = global_height - baseline

    c+=4 # sendId

    if 3 < version < 6:
        if distfieldpad is not None:
            FT2_bytearray[c:c+4] = pack(endianness+'f',distfieldpad)
        c+=4

    FT2_bytearray[c:c+4] = pack(endianness+'f',int_falsy(icgap)); c+=4

    if version == 4:
        if distfieldcenter is not None:
            FT2_bytearray[c:c+4] = pack(endianness+'f',distfieldcenter)
        c+=4
        if distfieldscale is not None:
            FT2_bytearray[c:c+4] = pack(endianness+'f',distfieldscale)
        c+=4

    if version > 5:
        if uses_SDF_texture is not None:
            FT2_bytearray[c:c+1] = pack(endianness+'?',uses_SDF_texture)
        c+=1

    posTable_list, chartable_list  = (
        generate_postable_chartable(json_info["chars"])
    )

    #postable
    if version > 2:
        c+=4 #ROTV

    posTable_size1_offset = c
    posTable_size, = unpack(endianness+'I',FT2_bytearray[c:c+4])
    c+=4

    attribute_names = ("x","y","width","height","baseline_offset","draw_offset","advance","page")
    attributes_count = 7 if version > 4 else 3

    # page is handled differently because it's a UInt8 unlike others which are floats

    postable_list_bytes = b""

    for current_pos in posTable_list:

        if hasExtraPerCharacterData and version > 4:

            current_pos["advance"] = (
                + current_pos.get("advance", 0)
                + current_pos["width"]
            )

            current_pos["y"] = (
                + current_pos["y"]
                + current_pos.get("top_crop", 0)
            )

            current_pos["height"] = (
                + global_height
                - current_pos.get("top_crop", 0)
                - current_pos.get("bottom_crop", 0)
            )

            current_pos["baseline_offset"] = (
                + current_pos.get("bottom_crop", 0) 
                - calculated_maxdescent
                + current_pos["height"]
            )

        for attr_name in attribute_names[:attributes_count]:
            
            postable_list_bytes += pack(
                endianness+'f',
                current_pos.get(attr_name, 0)
            )

        if version == 7:
            postable_list_bytes += pack(
                endianness+'B',
                current_pos.get("page", 0)
            )


    posTable_item_len = attributes_count * 4
    if version == 7:
        posTable_item_len += 1

    posTable_len_orig = posTable_item_len * posTable_size

    FT2_bytearray[c:c + posTable_len_orig] = bytearray(postable_list_bytes)
    c+= len(postable_list_bytes)
    posTable_size_packed = pack(endianness+'I', len(posTable_list))


    if posTable_size0_offset is not None:
        FT2_bytearray[posTable_size0_offset:posTable_size0_offset+4] = (
            posTable_size_packed
        )

    FT2_bytearray[posTable_size1_offset:posTable_size1_offset+4] = (
        posTable_size_packed
    )

    #chartable
    if version > 2:
        c+=4 # ROTV fourcc

    charTable_size1_offset = c
    charTable_size_orig, = unpack(endianness+'I',FT2_bytearray[c:c+4]); c+=4

    charTable_list_bytes = b''
    for char, pos_idx in chartable_list:
        charTable_list_bytes += char.encode("utf-16-"+endianness_le_be)
        charTable_list_bytes += pack(endianness+'H', pos_idx)

    FT2_bytearray[c:c + charTable_size_orig*4] = (
        bytearray(charTable_list_bytes)
    )
    c+= len(charTable_list_bytes)


    charTable_size_packed = pack(endianness+'I', len(chartable_list))

    if charTable_size0_offset is not None:
        FT2_bytearray[charTable_size0_offset:charTable_size0_offset+4] = (
            charTable_size_packed
        )

    FT2_bytearray[charTable_size1_offset:charTable_size1_offset+4] = (
        charTable_size_packed
    )

    if version > 1:
        kernTable_bytes = b''

        if version > 2:
            c+=4 #ROTV

        kernTable_size_old, = unpack(
            endianness+'I', FT2_bytearray[c:c+4]
        )
        kernTable_size_offset = c; c+=4
        kernTable_size_new = 0

        if "kerning" in json_info:
            for chars, gap in json_info["kerning"]:
                if len(chars)!=2 or not isinstance(chars,str):
                    continue
                charA, charB = chars
                kernTable_bytes+=(
                    charA.encode("utf-16-"+endianness_le_be)+
                    charB.encode("utf-16-"+endianness_le_be)+
                    pack(endianness+'f', gap)
                )
                kernTable_size_new+=1

        FT2_bytearray[kernTable_size_offset:kernTable_size_offset+4] = (
            pack(endianness+'I', kernTable_size_new)
        )

        FT2_bytearray[c:c+ kernTable_size_new*8] = bytearray(kernTable_bytes)

        c+= kernTable_size_new*8

    if version > 12:
        c+=4

    if version > 13:
        if singleChannelTexture is not None:
            FT2_bytearray[c:c+1] = pack('?',singleChannelTexture)
        c+=1

    filelen_packed = pack(endianness+'I', c - 4)
    FT2_bytearray[filelen_offset:filelen_offset+4] = filelen_packed 

    FT2_bytes = bytes(FT2_bytearray)
        
    if dds_bytes is not None:
        FT2_bytes = reimport_FT2_replace_dds(FT2_bytes,dds_bytes,endianness)

    return FT2_bytes
            
def detect_font_format(font_bytes: bytes, reimport: bool=False) -> tuple[function,str]:
    """
    Return a function for parsing/reimporting a font and
    also the font's endianness.
    """

    if font_bytes[8:12] == b"TNFN" or font_bytes[8:12] == b"NFNT":
        return (
            parse_FT2 if not reimport else reimport_FT2,
            ">" if font_bytes[8:12] == b"TNFN" else "<"
        )

    if font_bytes[32:64] == b"\xFF"*32 or font_bytes[32:64] == b"\x00"*32:
        return (
            parse_lsw1_FNT if not reimport else reimport_lsw1_FNT,
            '>' if font_bytes[28]==0 and font_bytes[31]!=0 else '<'
        )

    if font_bytes[:4] != b"\x00"*4:
        return (
            parse_lsw2_FNT if not reimport else reimport_lsw2_FNT,
            (
                '>'
                if unpack(">I",font_bytes[4:8])[0] <
                unpack("<I",font_bytes[4:8])[0] else
                '<'
            )
        )

    else:
        return (
            parse_TCS_QFN if not reimport else reimport_TCS_QFN,
            (
                '>'
                if unpack(">I",font_bytes[12:16])[0] <
                unpack("<I",font_bytes[12:16])[0] else
                '<'
            )
        )

def override():
    """
    Write a json file based on the parsed font and a dds file if applicable.
    """

    font_bytes = font_file.read_bytes()
    font_bytesIO = BytesIO(font_bytes)
    font_reimport_function, endianness = detect_font_format(font_bytes)
    json_ready, DDS_bytes = font_reimport_function(font_bytes, endianness)

    chardict = json_ready["chars"]

    chars_list_string = (
        "[\n" +
        ",\n".join(json.dumps(item, ensure_ascii=False) for item in chardict) +
        "\n]"
    )

    kerning_list_string = ""
    if "kerning" in json_ready:
        kerndict = json_ready["kerning"]
        kerning_list_string = (
        "[\n" +
        ",\n".join(json.dumps(item, ensure_ascii=False) for item in kerndict) +
        "\n]"
    )

    miscinfo = {x:json_ready[x] for x in used_attribute_names.keys()}

    json_info_string = json.dumps(miscinfo, ensure_ascii=False, indent=0)[2:-2]

    json_string = (
        '{' +
        json_info_string +
        f',\n"chars": {chars_list_string}'
    )

    if "kerning" in json_ready:
        json_string+=f',\n"kerning": {kerning_list_string}'

    json_string+="}"

    json_file.write_text(
        json_string,
        encoding="utf-8", newline="\n")

    if not DDS_bytes:
        return

    if DDS_bytes[:4] == b"DDS ":
        DDS_file.write_bytes(DDS_bytes)
    else:
        DDS_file.write_bytes(DDS_bytes) # TODO: add console texture deswizzling for extraction and reswizzling for importing

def reimport():
    json_info = None
    DDS_bytes = None

    if json_file.exists():
        json_info = json.loads(
            json_file.read_text(encoding="utf-8")
        )

    if DDS_file.exists():
        DDS_bytes = DDS_file.read_bytes()

    font_bytes = font_file.read_bytes()
    font_bytesIO = BytesIO(font_bytes)

    font_reimport_function, endianness = detect_font_format(
        font_bytes, reimport=True
    )

    font_bytes_new = font_reimport_function(
        font_bytes, endianness, json_info, DDS_bytes
    )

    font_file.write_bytes(font_bytes_new)

    

def main():

    if json_file.is_file() or DDS_file.is_file():
        action=input(
            "DDS and/or JSON files with the same name already exist.\n"
            "Do you want to [0] override them or [1] reimport them to the FT2? "
        )
        if action=="0" or not action:
            override()
        else:
            reimport()
    else:
        override()
    input("Finished")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("Press enter to exit.")