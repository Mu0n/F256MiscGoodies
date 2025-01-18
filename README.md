# F256MiscGoodies
Hodge Podge of useful files for the F256 series of computers I couldn't find a web source for.
I dumped them in this very repo whose readme you're currently reading. The issue is that discord links to files eventually die off, and the wiki would eventually have broken links. This is an attempt to circumvent this problem.
The real solution is to ask of each developper to publish their work, possibly from a github account or similar.

## Software at https://github.com/Mu0n/F256MiscGoodies/tree/main/apps
(from https://wiki.f256foenix.com/index.php?title=Software)
### Demos
fnxmas23 - 2 versions: fnxmas23_latest_OLD_FPGALoad.pgz and fnxmas23_latest_NEW_FPGALoad.pgz
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

