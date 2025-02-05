# F256MiscGoodies
Hodge Podge of useful files for the F256 series of computers I couldn't find a web source for.
I dumped them in this very repo whose readme you're currently reading. The issue is that discord links to files eventually die off, and the official F256 series wiki at https://wiki.f256foenix.com/index.php?title=Main_Page would eventually have broken links. This is an attempt to circumvent this problem.
The real solution is to ask of each developper to publish their work, possibly from a github account or similar.

## FPGA Loads at 
(from https://wiki.f256foenix.com/index.php?title=FPGA_Releases)  

### K2 "Black" (first rev boards sold as K2B in 2024)
Classic CNTX1, https://github.com/Mu0n/F256MiscGoodies/tree/main/fpga/K2/CNTX1  

Extended CNTX2, https://github.com/Mu0n/F256MiscGoodies/tree/main/fpga/K2/CNTX2/

### K2 "Purple" (release boards sold as K2 in 2025)
soon

### Jr2

Classic, https://github.com/Mu0n/F256MiscGoodies/tree/main/fpga/Jr2/Classic  

Extended, https://github.com/Mu0n/F256MiscGoodies/tree/main/fpga/Jr2/Extended  

6809 https://github.com/Mu0n/F256MiscGoodies/tree/main/fpga/Jr2/6809

### K

Classic, https://github.com/Mu0n/F256MiscGoodies/tree/main/fpga/K  

Extended, 

### Jr.

Classic, https://github.com/Mu0n/F256MiscGoodies/tree/main/fpga/Jr/Classic  

Extended, 

## Software at https://github.com/Mu0n/F256MiscGoodies/tree/main/apps
(from https://wiki.f256foenix.com/index.php?title=Software)
### Demos
fnxmas23 - 2 versions: fnxmas23_latest_OLD_FPGALoad.pgz and fnxmas23_latest_NEW_FPGALoad.pgz  
xmas24_k2 - a version of fnxmas23 that runs on Jr2 and K2 (no hardware machine ID lock out)
### Games
cosmic-1111.zip  
kartdemo.pgz  
lk_f256_1.0b19_demo.zip
### Game Jam 01
impasse.pgz  
soccur.pgz (game)  
soccur-2024-04-08.zip (source code)  
Flight Simulator - broken link, lost software
### Music
EdInHisLib.pgz  
jrtracker.bas (1 PSG with 3 channels)  
JrTracker-Manual.pdf  
xmas.trk (song for jrtracker.bas)  
tracker2.bas (2 PSG with 6 channels)  
goodvib.tr2 (song for tracker2.bas)  
modo_f256jr.rar

## Tools at https://github.com/Mu0n/F256MiscGoodies/tree/main/tools

### png2raw.py

(more useful scripts can be found at the source https://github.com/cmassat/EffenX/tree/dev/util)
(by SprySloth) Python script to take png indexed mode files to a format that can be used for the Foenix.  Just make sure in aseprite to import palette from  image with 256 colors and switch the image to index mode. Run the script as  

_python png2raw.py filename.png outputfilename widthInPixels, heightInPixels_  <--This is for each sprite, not the whole image size.   

This will create 2 files with .bin and .pal.  Bin is for the image data, and pal is for the palette data.

prior step to make it work:

_pip install pillow_

### bmp2LCD.py

this will convert bitmap files to a format that can be sent to the F256K2's LCD screen, which accepts 240x320 images, but the visible part is really 240x280 pixels, with 20 line bands up top and down at the bottom that aren't really visible.
In order to send the converted binary file to the LCD, consult my other repo for some example C code: https://github.com/Mu0n/F256KsimpleCdoodles
The bitmap has to be exported as a R5G6B6 palette, this is doable under advanced export options in Gimp, for instance.

_python bmp2LCD.py filename.bmp outputfilename_

