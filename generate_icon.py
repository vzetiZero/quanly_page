from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

res_dir = Path(__file__).resolve().parent / "resources"
res_dir.mkdir(exist_ok=True)
icon_png = res_dir / "icon.png"
icon_ico = res_dir / "icon.ico"

# create a simple dark rounded square with 'FB' initials
size = 512
bg_color = (10, 25, 40)
accent = (37, 99, 235)
text_color = (255, 255, 255)

img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# rounded rectangle
radius = 80
for y in range(size):
    for x in range(size):
        # simple circle mask for rounded corners
        if (x < radius and y < radius and (x - radius) ** 2 + (y - radius) ** 2 > radius ** 2) or \
           (x < radius and y >= size - radius and (x - radius) ** 2 + (y - (size - radius)) ** 2 > radius ** 2) or \
           (x >= size - radius and y < radius and (x - (size - radius)) ** 2 + (y - radius) ** 2 > radius ** 2) or \
           (x >= size - radius and y >= size - radius and (x - (size - radius)) ** 2 + (y - (size - radius)) ** 2 > radius ** 2):
            continue
        img.putpixel((x, y), bg_color + (255,))

# draw accent band
draw.rectangle([(0, size * 0.6), (size, size)], fill=(20, 40, 60))

# draw initials
try:
    font = ImageFont.truetype("arial.ttf", 200)
except Exception:
    font = ImageFont.load_default()

bbox = draw.textbbox((0, 0), "FB", font=font)
w = bbox[2] - bbox[0]
h = bbox[3] - bbox[1]
draw.text(((size - w) / 2 - bbox[0], (size - h) / 2 - 20 - bbox[1]), "FB", font=font, fill=text_color)

img.save(icon_png, format="PNG")
# create .ico with multiple sizes
icon_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
icons = []
for s in icon_sizes:
    icons.append(img.resize(s, Image.LANCZOS))
icons[0].save(icon_ico, format="ICO", sizes=icon_sizes)

print(f"Generated icon files: {icon_png} and {icon_ico}")
