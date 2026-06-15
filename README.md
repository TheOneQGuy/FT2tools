# FT2tools
Two tools for the Tt Games FT2 font format, one for extracting to json+dds and recompiling, the other one for TTF to json+dds so it can be recompiled with the first one. I might make FT2 to TTF too. To download, you can click the green Code button and press Download ZIP, or you can download the files individually.

## FT2 extract and reimport
Can extract FT2 into a json+dds and also reimport from json+dds into FT2.

The json contains the characters along with their x, y, and width on the dds. The height is globally adjusted. Some FT2 versions have more attributes per character, but those are rarely used.

Supports most FT2 versions, it doesn't support button FT2s (version 02) and LCU FT2s (version 04). It has also not been tested on TSS FT2s. However most games can read FT2s from previous games just fine.

Requires Python to use. There's no GUI, drag your FT2 to the .py file; If a json or dds with the same name as the FT2 is found, it will give you the option to reimport.

Note: Vanilla spaces use x=0 and y=0 and the game simply doesn't draw anything for them, even though it would usually be an exclamation mark that is cut off. This tool sometimes breaks those spaces and ingame the cut off exclamation mark would actually be shown. To fix this, just set the space's x and y to somewhere that is empty in the image. *This tool was coded by me.*

## TTF to FT2
Can convert TTF fonts to json+dds which can be reimported to an FT2. It also gives you the option for outlines and kerning.

Requires Python + pillow and fonttools (Install using `py -m pip install pillow fonttools` in command prompt after installing Python). This one *does* have a GUI and you just double click it to open it. It is recommended to use the size that the FT2 you're going to import to uses. *This tool was coded by AI*
