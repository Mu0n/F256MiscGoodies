# Coded by Mu0n aka 1BitFeverDreams
# this computes 88 low byte and high byte values for the PSG chip inside the F256 line of computers
# Foenix Retro Systems. I was using 88 to match the 88 keys of my M-Audio keyboard controller
# a lot of these notes have to be discarded unfortunately since it's only a 10-bit resolution
# split into 6 bits meant for the high byte and 4 bits meant for the low byte
#
# The bit configuration is as follows, as can be read more in detail in the F256Jr manual p.55 in the sound chapter
# high byte: 0 X F9 F8 F7 F6 F5 F4     X= is unused and can be set to 0
#  low byte: 1 0  0  0 F3 F2 F1 F0   000= set for tone 1, 010= set for tone 2, 100= set for tone 3
#
# the first note it yields is an 'A' at 27.5 Hz, which would be 4 octaves below 440 Hz A.
# the frequency of those first 2 octaves makes it so that the hex number that must be chopped out
# surpasses the 10 bit ceiling of decimal value 1023
# the audible range thus starts at 110 Hz A, 2 octaves below 440 Hz A with decimal value 1014 
#
# The computation is as follows. The Master clock value of 3 570 000 MHz is divided by (32 * Freq_in_Hz) of your note
# Then, round down the float into an integer
# Convert to Hex source value
# Mask out bits 0b0000 0011 1111 0000 for the high part, shift them right 4 times and you got the high byte ready
# Go back to the Hex source value
# Mask out bits 0b0000 0000 0000 1111 for the low part, but add bit 0b1000 0000 with an or bitwise operation
# and adjust bits 4,5,6 out of 7 to select the right psg channel (000= set for tone 1, 010= set for tone 2, 100= set for tone 3)

import math

# Number of elements in the lookup table
num_elements = 88

# Generate the lookup table
psgLow = []
psgHigh = []

masterClock = 3.57e6
for j in range(-4,4):
    for i in range(0,12):
        if(j==3 and i>3):
            break
        freq = 440 * pow(2,j) * pow(2,i/12)
    
        n=math.floor(masterClock/(32*freq))
        hexNum = hex(n)
        #print("i ",i," j ",j,"mult ",pow(2,j),"value ",freq, " n", n, "hex ",hexNum)
        hiPart = n & 0b1111110000
        hiPart = hiPart >> 4
        loPart = n & 0b0000001111
        loPart = loPart | 0b10000000
       # print("hi ",hex(hiPart), "lo ",hex(loPart))
        
        psgLow.append(hex(loPart))
        psgHigh.append(hex(hiPart))

print("uint8_t psgLow[] = {")
for i in range(num_elements):
    print(psgLow[i], end="")
    if(i==87):
        break
    else:
        if(i%12 !=0):
            print(",", end="")
        else:
            print(",")
print("};")

print("uint8_t psgHigh[] = {")
for i in range(num_elements):
    print(psgHigh[i], end="")
    if(i==87):
        break
    else:
        if(i%12 !=0):
            print(",", end="")
        else:
            print(",")
print("};")
