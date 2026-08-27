# FT2tools
Tools for the Tt Games FT2 font format, one for extracting to json+dds and recompiling, the other ones for various things you can do with the dds and json files but automated. You can all tools at once by clicking the green "Code" button and then "Download ZIP". Alternatively if you aren't much familiar with running Python scripts, you can used compiled EXE versions found in the Releases section (Windows only).

You can see the list of planned additions and fixes by going to the Issues tab, you can also suggest your own additions or fixes there.

## FT2 extract and reimport
Can extract FT2 (and other Tt font formats) into a json+dds and also reimport from json+dds into FT2.

The json contains some file flags as well as the characters along with their x, y, and width on the dds. Some FT2 versions have more attributes per character.

Extracting support:
- All FT2 versions 01-0E (1-14)
- LSW1 FNT
- LSW2+ FNT
- QFN, CQF, UFN

Reimporting support:
- All FT2 versions 01-0E (1-14)
- (more soon)

Each font can have these possible header values (Not all versions have each field):
- `global_height`: All glyphs use a shared height. This is that height.
- `baseline`: Baseline, only does anything for some games, stored regardless.
- `tracking`: Space between characters, usually just 0. Doesn't work on all games, stored regardless.
- `max_descent`: Only does anything if height_cropping is enabled. I don't really know what it does though. It is usually `global_height - baseline`.
- `height_cropping`: true or false, whether `top_crop` and `bottom_crop` values per each character are used. `height_cropping` is only an option for FT2 version 05+.
- `single_channel_texture`: I don't even know what this does. But it seems to be true for all files I've found with it.
- `SDF_texture`: true or false, whether the font texture is a [Signed Distance Field texture](https://www.google.com/search?q=Signed+Distance+Field+font&udm=2) or not.
- `SDF_pad`: Pad value for the SDF texture if applicable.
- `SDF_center`: Center value for the SDF texture if applicable.
- `SDF_scale`: Scale value for the SDF texture if applicable.
- `chars`: Dictionary of all characters in the font.
- `kerning`: List of kernings for each two characters. Item example: `["Ta",-4.0]`

Each glyph in `chars` can have these fields:
- `char`: A string containing the glyph for this entry.
- `x`: X value of the left of the glyph in the dds in pixels.
- `y`: Y value of the left of the glyph in the dds in pixels.
- `width`: Width of the glyph in the dds in pixels. Can be negative.
- `top_crop`: How many pixels of the top of the glyph to not render. Will only work if `height_cropping` is enabled.
- `bottom_crop`: How many pixels of the bottom of the glyph to not render. Will only work if `height_cropping` is enabled.
- `draw_offset`: X offset for drawing this glyph.
- `advance`: X offset for drawing the next glyph.
- `page`: Only in version 08, which I have not seen at all so I don't know for sure what it does and how it works.
- `special`: Either "space" or "string_ending".
  - "space" means the character is not rendered at all and all other fields are ignored except the `width`.
  - "string_ending" means any text will only be rendered up to before the first occurrence of the `char` in the text being rendered.

## TTF to FT2
Can convert TTF fonts to json+dds by rendering the vectors in the dds, which can be reimported to an FT2. It also gives you the option for outlines and kerning.

Requires the Pillow and fonttools modules (Unless using pre-compiled EXE version). Has a GUI and you just double click it to open it. It is recommended to use a global height around the same height as the font you are reimporting to.

*AI help was used for making this tool.*

## FT2 merge
Allows you to merge multiple FT2 files together. Technically doesn't merge the FT2 files themselves but rather merges the extracted json+dds files, which you can re-imported into an actual FT2.

Avoids double DXT compression by copying 4x4 DXT blocks from the input dds directly. On files with very little space between glyphs, this means that some parts of other glyphs will be in the dds too, but the json width only includes the width of the glyph itself and so this has no visible artifacts ingame.

The top-most font is preferred and only glyphs that are not included in the top font are gotten from the bottom ones.
Requires the Pillow module (Unless using pre-compiled EXE version). it has a GUI and you just double click it to open it.

*AI help was used for making this tool.*
