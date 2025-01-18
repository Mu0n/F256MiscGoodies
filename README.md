# F256MiscGoodies
Hodge Podge of useful files for the F256 series of computers I couldn't find a web source for

## Tools

### png2raw.py

(by SprySloth) Python script to take png indexed mode files to a format that can be used for the Foenix.  Just make sure in aseprite to import palette from  image with 256 colors and switch the image to index mode. Run the script as  

_python png2raw.py filename.png outputfilename widthInPixels, heightInPixels_  <--This is for each sprite, not the whole image size.   

This will create 2 files with .bin and .pal.  Bin is for the image data, and pal is for the palette data.

prior step to make it work:

_pip install pillow_

