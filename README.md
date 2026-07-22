# FT2tools
Tools for the Tt Games FT2 font format, one for extracting to json+dds and recompiling, the other ones for various things you can do with the dds and json files but automated. You can all tools at once by clicking the green "Code" button and then "Download ZIP". Alternatively if you aren't much familiar with running Python scripts, you can used compiled EXE versions found in the Releases section (Windows only).

All of these tools are written in Python and you need it installed in order to use them.

## FT2 extract and reimport
Can extract FT2 into a json+dds and also reimport from json+dds into FT2.

The json contains the characters along with their x, y, and width on the dds. The height is globally adjusted. Some FT2 versions have more attributes per character, but those are rarely used.

Supports most FT2 versions, it doesn't support button FT2s (version 02) and LCU FT2s (version 04). It has also not been tested on TSS FT2s. However most games can read FT2s from previous games just fine.

There's no GUI, drag your FT2 to the .py file; If a json or dds with the same name as the FT2 is found, it will give you the option to reimport. (Note: in the latest version of python, to give arguments, you need to have "py" or "python" or any path to the python exe at the start of the command, else it won't work. This means that dragging to the py file will not work anymore and you have to manually give the arguments from cmd like this: `py ".py file path" ".ft2 file path"`

*This tool was coded by me.*

## TTF to FT2
Can convert TTF fonts to json+dds which can be reimported to an FT2. It also gives you the option for outlines and kerning.

Requires Pillow (Install using `py -m pip install pillow` in command prompt after installing Python). Has a GUI and you just double click it to open it. It is recommended to use the size that the FT2 you're going to import to uses.

*This tool was coded by AI*

## FT2 merge
Allows you to merge multiple FT2 files together. Technically doesn't merge the FT2 files themselves but rather merges the extracted json+dds files, which you can re-imported into an actual FT2.

Avoids double DXT compression by copying 4x4 DXT blocks from the input dds directly. On files with very little space between glyphs, this means that some parts of other glyphs will be in the dds too, but the json width only includes the width of the glyph itself and so this has no visible artifacts ingame.

<img width="492" height="210" alt="image" src="https://github.com/user-attachments/assets/5f2e1a71-102d-40b9-b16e-de00f310551b" />

The top-most font is preferred and only glyphs that are not included in the top font are gotten from the bottom ones.

Requires Pillow (Install using `py -m pip install pillow` in command prompt after installing Python). it has a GUI and you just double click it to open it.

*This tool was coded by AI*
