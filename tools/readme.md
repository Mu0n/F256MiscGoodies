### Wildbits Graphics Converter

A small windowed tool for Wildbits retro-computer developers that turns an indexed-color PNG image into the raw palette (.pal) and bitmap (.bin) files those machines expect -- or, in K2 Mini-LCD mode, into an R5G6B5 binary for the K2 case's tiny embedded screen.

<img width="1241" height="843" alt="image" src="https://github.com/user-attachments/assets/acaa32c8-0371-499b-9470-438676b9e35f" />

### Wildbits Memory Planner

A small windowed tool for Wildbits retro-computer developers that lets you place data objects on a memory map, compact them, resize them, move them. Once you're done, export the list of everything with start address and size. Export a llvm-mos or oscar64 list of #embed or #pragma directives for easy integration in your C project or otherwise.

<img width="2556" height="1388" alt="image" src="https://github.com/user-attachments/assets/7257ed2a-0eab-4a31-ad91-178fddd8ac72" />

### bmp2LCD.py

this will convert bitmap files to a format that can be sent to the F256K2's LCD screen, which accepts 240x320 images, but the visible part is really 240x280 pixels, with 20 line bands up top and down at the bottom that aren't really visible.
In order to send the converted binary file to the LCD, consult my other repo for some example C code: https://github.com/Mu0n/F256KsimpleCdoodles
The bitmap has to be exported as a R5G6B6 palette, this is doable under advanced export options in Gimp, for instance.

_python bmp2LCD.py filename.bmp outputfilename_

### png2raw.py

(more useful scripts can be found at the source https://github.com/cmassat/EffenX/tree/dev/util)
(by SprySloth) Python script to take png indexed mode files to a format that can be used for the Foenix.  Just make sure in aseprite to import palette from  image with 256 colors and switch the image to index mode. Run the script as  

_python png2raw.py filename.png outputfilename widthInPixels, heightInPixels_  <--This is for each sprite, not the whole image size.   

This will create 2 files with .bin and .pal.  Bin is for the image data, and pal is for the palette data.

prior step to make it work:

_pip install pillow_

### PSGvalues.py

Generate C language arrays for the frequencies needed for notes for the PSG. Will be default use the tone 1 target, so the high nybble of the low frequency will be 8 hex. change to A or C for tone 2 and tone 3.


### sinelookup.py

Simple sinus look-up table generator. edit away the parameters to have more or less elements, amplitude, etc.
