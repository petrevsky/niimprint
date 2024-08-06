from io import BytesIO
from PIL import Image, ImageOps
import barcode
from wand.image import Image as WandImage
from wand.drawing import Drawing as WandDrawing
from wand.color import Color
from barcode.writer import ImageWriter
import requests
import os


def calculate_dimensions(label_width_mm, label_height_mm, pixels_per_mm=8):
    width_px = int(label_width_mm * pixels_per_mm)
    height_px = int(label_height_mm * pixels_per_mm)
    return width_px, height_px


def generate_barcode(barcode_text, available_width, barcode_height):
    barcode_class = barcode.get_barcode_class("code128")
    barcode_writer = ImageWriter()
    barcode_writer.set_options({})
    barcode_image = barcode_class(barcode_text, writer=barcode_writer)
    barcode_buffer = BytesIO()
    barcode_image.write(barcode_buffer, options={"write_text": False, "quiet_zone": 0})
    barcode_img = Image.open(barcode_buffer)
    return barcode_img.resize((available_width, barcode_height), Image.NEAREST)


def render_text_with_wand(text, font_path, font_size):
    with WandImage(width=1, height=1) as tmp_image:
        with WandDrawing() as draw:
            draw.font = font_path
            draw.font_size = font_size
            draw.fill_color = Color("black")
            draw.resolution = (900, 900)

            metrics = draw.get_font_metrics(tmp_image, text)
            width = int(metrics.text_width)
            height = int(metrics.text_height)

            with WandImage(
                width=width, height=height, background=Color("transparent")
            ) as text_layer:
                draw.text(0, int(metrics.ascender), text)
                draw(text_layer)

                text_layer.format = "png"
                text_layer.alpha_channel = "activate"
                text_buffer = BytesIO(text_layer.make_blob("png"))

    return Image.open(text_buffer).convert("RGBA")


def generate_barcode_image(
    barcode,
    label_width_mm,
    label_height_mm,
    domain,
    include_id=False,
):
    horizontal_offset = 12
    vertical_offset = 5

    pixels_per_mm = 8
    width_px, height_px = calculate_dimensions(
        label_width_mm, label_height_mm, pixels_per_mm
    )

    print(f"Label dimensions: {width_px}px x {height_px}px")

    response = requests.get(f"https://{domain}/api/purchase-order?barcode={barcode}")

    if response.status_code != 200:
        print(response.text)
        raise RuntimeError("Failed to fetch data")

    js = response.json()

    data = js["purchaseOrder"]
    product_name = data["name"]
    product_name_alb = data["nameAlb"]
    plt_company_name = data["pltCompany"]["name"]
    plt_company_name_alb = data["pltCompany"]["nameAlb"]
    plt_company_location = data["pltCompany"]["location"]
    plt_company_location_alb = data["pltCompany"]["locationAlb"]
    register_id = data["product"]["id"]

    barcode_text = barcode

    if include_id:
        barcode_text += f" | {register_id}"

    product_data = [
        f"Артикл:  {product_name}",
        f"Увозник:  {plt_company_name}",
        f"Седиште:  {plt_company_location}",
        f"Потекло: Кина   Origjina: Kina",
        f"Artikulli:  {product_name_alb}",
        f"Importues:  {plt_company_name_alb}",
        f"Shtabi:  {plt_company_location_alb}",
    ]

    path = os.path.dirname(os.path.abspath(__file__))
    font_path = f"{path}/fonts/HarmonyOS_Sans_Regular.ttf"

    image = Image.new("RGB", (width_px, height_px), color=(255, 255, 255))

    product_font_size = int(height_px * 0.072)
    spacing = int(height_px * 0.005)
    current_y = 0

    for text in product_data:
        text_image = render_text_with_wand(text, font_path, product_font_size)
        text_width, text_height = text_image.size
        image.paste(text_image, (0, current_y), text_image)
        current_y += text_height + spacing

    barcode_height = int(height_px * 0.30)

    barcode_width = width_px - horizontal_offset * 3
    barcode_img = generate_barcode(barcode, barcode_width, barcode_height)

    # Center the barcode horizontally
    barcode_x = (width_px - barcode_img.width) // 2
    image.paste(barcode_img, (0, current_y))

    barcode_font_size = int(height_px * 0.09)
    barcode_text_image = render_text_with_wand(
        barcode_text, font_path, barcode_font_size
    )
    barcode_text_width, barcode_text_height = barcode_text_image.size

    # Center the barcode text below the barcode
    barcode_text_x = (barcode_width - barcode_text_width) // 2

    barcode_text_y = current_y + barcode_height + spacing
    image.paste(
        barcode_text_image, (barcode_text_x, barcode_text_y), barcode_text_image
    )

    # Apply horizontal offset
    if horizontal_offset > 0:
        image = ImageOps.expand(
            image, border=(horizontal_offset, 0, 0, 0), fill=(255, 255, 255)
        )
    elif horizontal_offset < 0:
        image = image.crop((-horizontal_offset, 0, image.width, image.height))

    # Apply vertical offset
    if vertical_offset > 0:
        image = ImageOps.expand(
            image, border=(0, vertical_offset, 0, 0), fill=(255, 255, 255)
        )
    elif vertical_offset < 0:
        image = image.crop((0, -vertical_offset, image.width, image.height))

    return image
