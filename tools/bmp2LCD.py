import argparse
from PIL import Image

def convert_bmp_to_raw(image_path, output_bin):
    image = Image.open(image_path)
    
    # Ensure the image is in RGB mode
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    pixels = image.load()
    width, height = image.size
    
    binary_data = bytearray()

    for y in range(height):
        for x in range(width):
            pixel = pixels[x, y]
            red = (pixel[0] >> 3) & 0x1F  # 5-bit red
            green = (pixel[1] >> 2) & 0x3F  # 6-bit green
            blue = (pixel[2] >> 3) & 0x1F  # 5-bit blue

            rgb565 = (red << 11) | (green << 5) | blue

            binary_data.append(rgb565 & 0xFF)  # Low byte
            binary_data.append((rgb565 >> 8) & 0xFF)  # High byte

    with open(output_bin, 'wb') as bin_file:
        bin_file.write(binary_data)

    print(f"R5G6B5 binary data has been written to {output_bin}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert BMP (R5G6B5) to binary file without header.")
    parser.add_argument("image_path", type=str, help="Path to the input BMP image.")
    parser.add_argument("output_bin", type=str, help="Name of the output binary file.")
    args = parser.parse_args()

    convert_bmp_to_raw(args.image_path, args.output_bin)
