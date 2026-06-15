from sys import argv
from pathlib import Path
import struct
import json
import traceback

attributes=[]
chardict=[]
try:
    FT2 = Path(argv[1])
except:
    input("Drag an FT2 to the .py file.")
else:
    json_file=FT2.with_suffix(".json")
    dds_file=FT2.with_suffix(".dds")

def get_ROTV_section(file_bytes:bytes, idx:int=0, returns_offset:bool=False):
    if contains_ROTV:
        file_bytes = file_bytes.replace(b"ROTV",b"NOTV", idx)
        ROTVpos = file_bytes.index(b"ROTV")
    else:
        ROTVpos = 43
        if idx:
            ROTVpos+=8+len(get_ROTV_section(file_bytes, 0))
    section_len = int(file_bytes[ROTVpos+4:ROTVpos+8].hex(), 16)
    #print(section_len)
    attributes_count_local=attributes_count
    if idx:
        attributes_count_local=1 #will only set it locally
    return (file_bytes[ROTVpos+8 : ROTVpos+8+section_len*attributes_count_local*4]
            if not returns_offset else
            ROTVpos)
    
def get_global_height(file_bytes:bytes, returns_offset:bool=False):
    current_offset=get_ROTV_section(file_bytes, 0, returns_offset=True)-8-1
    
    #Go left till you find a non-00 byte, then go left till you find a 00 byte
    while file_bytes[current_offset] == 0:
        print("a",current_offset, hex(file_bytes[current_offset]))
        current_offset-=1

    while file_bytes[current_offset] != 0:
        print("b",current_offset, hex(file_bytes[current_offset]))
        current_offset-=1

    #Now we're at the start of the third value in the header. Global height is the first one so just go back 8 bytes
    current_offset-=7

    if returns_offset:
        return current_offset
    else:
        return (
            struct.unpack('>f',
                file_bytes[
                    current_offset:
                    current_offset+4]
                    )[0]
        )


def get_file_info(file_bytes:Path):


    global contents; contents = file_bytes.read_bytes()
    global version; version = contents[15]
    global contains_ROTV; contains_ROTV = b'ROTV' in contents
    global attributes_count
    attributes_count = 7 if version>3 else 3
    attributes_count = 8 if version==7 else attributes_count
    global attribute_names
    attribute_names=("x","y","width","height","x_offset","y_offset","advance","page")

    print(f"Version {version}")
    print(f"ROTV attributes: {attributes_count}: {attribute_names[:attributes_count]}")
    print(f'Uses "ROTV": {contains_ROTV}')

    global dds_pos; dds_pos = contents.index(b"DDS |")
    global dds_contents; dds_contents=contents[dds_pos:]

def decode_file():
    # Section 1 where W is Width, H is Height, T is Top, L is Left, A is Advance, P is Page
    # XX XX XX XX YY YY YY YY WW WW WW WW (V3)
    # XX XX XX XX YY YY YY YY WW WW WW WW HH HH HH HH TT TT TT TT LL LL LL LL AA AA AA AA (V4+ != 7)
    # XX XX XX XX YY YY YY YY WW WW WW WW HH HH HH HH TT TT TT TT LL LL LL LL AA AA AA AA PP PP PP PP (V7)
    global contents
    section = get_ROTV_section(contents, 0)
    global global_height; global_height = get_global_height(contents)

    c=0
    try:
        while True:
            current_attributes={}
            for i in range(attributes_count):
                raw=section[c:c+4]
                float32=struct.unpack('>f',raw)[0]
                current_attributes[attribute_names[i]]=(int(float32))
                c+=4
            attributes.append(current_attributes)
    except struct.error:
        pass
    except ValueError:
        pass

    #Section 2 (CC CC II II) where C is character, I is index for section 1.
    section = get_ROTV_section(contents, 1)
    c=0
    while len(section)>3:
        char=section[c:c+2].decode("utf-16-be")
        try:
            idx=int(section[c+2:c+4].hex(), 16)
        
            chardict.append({"char":char,**attributes[idx]})
        except IndexError, ValueError:
            break
        c+=4

    #Section 3 - just dds, exported in get_file_info()

def json_to_tnfn(file_text:Path):

    json_file_contents=json.loads(file_text.read_text(encoding="utf-8", newline="\n"))
    json_list=json_file_contents["chars"]
    try:
        new_global_height=json_file_contents["global_height"]
    except KeyError:
        new_global_height=60
    used_attribute_names=attribute_names[:attributes_count]
    values_to_write=b'\x00'*(attributes_count*4)
    chars_to_write=b''

    print(used_attribute_names)
    # Important: game only accepts FT2  with chars in unicode order
    json_list.sort(key=lambda x: x["char"]) 

    global json_entries_number; json_entries_number=len(json_list)
    for i in range(json_entries_number):
        for j in used_attribute_names:
            try:
                current_val=json_list[i][j]
            except KeyError:
                current_val=0
            values_to_write+=(struct.pack('>f',current_val))
        current_char = json_list[i]["char"].encode("utf-16-be")
        chars_to_write+=(current_char
                         +(i+1).to_bytes(2, byteorder="big"))

    print(len(values_to_write),len(chars_to_write))

    global contents
    global contentsnew

    ROTV1_old=get_ROTV_section(contents, 0)
    ROTV2_old=get_ROTV_section(contents, 1)


    section2_newlen = json_entries_number.to_bytes(4, byteorder="big")
    section1_newlen = (json_entries_number+1).to_bytes(4, byteorder="big")

    ROTV1len_offset = get_ROTV_section(contents, 0, returns_offset=True)+4
    ROTV2len_offset = get_ROTV_section(contents, 1, returns_offset=True)+4

    print(
        contents[ROTV1len_offset:ROTV1len_offset+4].hex(),
        contents[ROTV2len_offset:ROTV2len_offset+4].hex(),
        section1_newlen.hex(),
        section2_newlen.hex(),
        "",
        ROTV2len_offset
    )

    #replace sections len in header and for first ROTV
    contentsnew = (
        contents[:ROTV1len_offset+4]
        .replace(
            contents[ROTV1len_offset:ROTV1len_offset+4],
            section1_newlen)
        .replace(
            contents[ROTV2len_offset:ROTV2len_offset+4],
            section2_newlen)
        +contents[ROTV1len_offset+4:]
    )

    #replace sections len for second ROTV
    contentsnew = (
        contentsnew[:ROTV2len_offset]
        + section2_newlen
        + contentsnew[ROTV2len_offset+4:]
    )

    # print(contentsnew.count(ROTV2_old))
    # print(chars_to_write[:32].hex())
    # print(ROTV2_old[:32].hex())

    #replace section 1 & 2
    contentsnew = (
        contentsnew
        .replace(ROTV1_old,values_to_write)
        .replace(ROTV2_old,chars_to_write)
    )

    #replace whole file len
    contentsnew = (
        len(contentsnew[4:])
        .to_bytes(4, byteorder="big")
        +contentsnew[4:]
    )

    #replace global height
    global_height_offset=get_global_height(contentsnew, returns_offset=True)
    contentsnew = (
        contentsnew[:global_height_offset]
        +struct.pack('>f', new_global_height)
        +contentsnew[global_height_offset+4:]
        
    )

    contents=contentsnew

def main():

    get_file_info(FT2)
    if json_file.is_file() or dds_file.is_file():
        action=input("Do you want to override (0) or reimport (1)?")
        if action=="0" or not action:
            override()
        else:
            reimport()
    else:
        override()
    input("Finished")

def override():
    
    decode_file()
    chars_list_json_string = "[\n" + ",\n".join(json.dumps(item, ensure_ascii=False) for item in chardict) + "\n]"
    json_string = f'"global_height": {global_height}\n"chars": {chars_list_json_string}'
    json_file.write_text(
        json_string
        , encoding="utf-8", newline="\n")
    dds_file.write_bytes(dds_contents)

def reimport():
    global contents
    contents = contents[:dds_pos]
    if json_file.is_file():
        json_to_tnfn(json_file)
    if dds_file.is_file():
        contents+=dds_file.read_bytes()
    else:
        contents+=dds_contents
    FT2.write_bytes(contents)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("Press enter to exit.")