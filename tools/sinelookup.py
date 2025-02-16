import math

# Number of elements in the lookup table
num_elements = 256

# Amplitude of the sine wave
amplitude = 64

# Generate the lookup table
lookup_table = []
for i in range(num_elements):
    angle = 2 * math.pi * i / num_elements
    value = int(amplitude * math.sin(angle))
    lookup_table.append(value)

# Print the lookup table in C array format
print("signed char sine_lookup_table[256] = {")
for i, value in enumerate(lookup_table):
    if i % 8 == 0:
        print("\n    ", end="")
    print(f"{value:4d}, ", end="")
print("\n};")
