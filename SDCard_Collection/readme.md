## Collection of files (demos, games, apps, utilities)

### SDcurated_MAME_NOV30_2025.zip
Contains a .img image file meant to be used with F256 Mame emulator

### SDcurated_May28th_2026.zip
This is meant to be unzipped directly into a SD card destined to be used with a real Foenix machine. Just unzip into its root

#### SDCurated_mediaPack.zip
This adds a few more .mp3 files in the media/mp3/ folder, meant for playback with music/f256amp

#### SDCurated_mediaPack2.zip
This adds vgm files in your media/ folder, meant for playback with music/opl3snooper. If you need more vgm files, go to https://opl.wafflenet.com/ and download that whole archive!

### Latest changes (May 28th 2026):

* _apps/midiplayer has been updated to v2.7 from @Mu0n (it's the same as music/cozymidi.pgz)
* _apps/vgmplayer.pgz has been updated to v1.0 from @Mu0n  (it's the same as music/opl3snooper.pgz)

* core2x/spr128b.pgz has been added from @beethead

* demos/anim_memtext.pgz has been added, done by @jbaker8935, has a playlist.txt and a bunch of bins
* demos/gfxDemo.pgz has been added, done by @Mu0n
* demos/hackGfx.pgz has been added, done by @Mu0n, requires media/vgm/sshockt.vgm to be present at that media/vgm/ location
* demos/living.pgz Living Worlds has been added, done by @haydenkale
* demos/pendulum.pgz has been updated to work with 2x core from @MikeC
* demos/rpg-demos.bas has been added by @econtrerasd, an old Jr demo revamped for Jr2 and K2
* demos/vs1053b_3d_demo.pgz has been added from @jbaker8935

* GameJams/Dec25/hero.pgz port of H.E.R.O. done by @Cibee has been added
* GameJams/Dec25/leaderboard.pgz done Leaderboard by @xDraconian has been added
* GameJams/Dec25/pitfall.pgz Pitfall port done by @Minstrel Dragon has been added
* GameJams/Dec25/track.pgz Track Day done by @Mike has been added

* games_wip/grudge.pgz an 8-10 multiplayer game by @Mu0n has been added. Needs FNX4N4S
* games_wip/mario.pgz a 1983 Mario Bros. port done by @beethead has been added

* media/vgm/sshockt.vgm has been added, it's needed for demos/hackGfx.pgz

* music/jrtracker.bas from @econtrerasd has been moved to this location, originally in the root, and now interacts with files in media/trk/ instead of saving and loading from the root directory

* music/tracker2.bas from @econtrerasd has been moved to this location, originally in root and now interacts with files in media/tr2/ instead of saving and loading from the root directory

* toolkit.bas WildBits Graphic Toolkit superbasic program by @econtrerasd added (in the root), and this needs the directories (in the root)
* fonts/
* palettes/
* sprites/
* tiles/
* toolkit/

* demos/rpg-demo.bas also needs the following directories as well
* media/sprites/
* media/palettes/
* media/tiles/
* media/bitmaps/

* takeonme.trk from the root has been moved to media/trk/takeonme.trk and adjusted to 90 bpm tempo
* moonlight.tr2 has been deleted from the root
* moonst22.tr2 from the root has been renamed and moved to media/tr2/moonst23.tr2 and ajusted to 110 bpm tempo
* goodvib.tr2 from the root has been moved to media/tr2/goodvib.tr2
* xmas2.tr2 from the root has been moved to media/tr2/xmas2.tr2
* xmas.trk was added to media/trk/xmas.trk and adjusted to 90 bpm

NEW INITIATIVE:

if a program, demo, game, tool needs to have externally loaded files, then we should strive to use the centralized depot of media/palettes, sprites, bitmaps, tiles, etc. We should steer clear of using files in the root, or individual folders in the root. The old locations will stay for the transition period for the programs that haven't migrated to this new way of doing things.

