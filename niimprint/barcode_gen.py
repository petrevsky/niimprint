from io import BytesIO
from PIL import Image
import barcode
from wand.image import Image as WandImage
from wand.drawing import Drawing as WandDrawing
from wand.color import Color
from barcode.writer import ImageWriter
import requests
import json
import os


def calculate_dimensions(label_width_mm, label_height_mm, pixels_per_mm=8):
    width_px = int(label_width_mm * pixels_per_mm)
    height_px = int(label_height_mm * pixels_per_mm)
    return width_px, height_px


def calculate_padding(padding_mm, pixels_per_mm=8):
    return {k: int(v * pixels_per_mm) for k, v in padding_mm.items()}


def generate_barcode(barcode_text, available_width, barcode_height, font_path):
    barcode_class = barcode.get_barcode_class("code128")
    barcode_writer = ImageWriter()
    barcode_writer.set_options(
        {
            "module_width": 1,
            "module_height": 1,
            "write_text": False,
            "font_size": 0,
            "text": "test",
            "human": "test",
            "font_path": font_path,
        }
    )
    barcode_image = barcode_class(barcode_text, writer=barcode_writer)
    barcode_buffer = BytesIO()
    barcode_image.write(barcode_buffer, options={"write_text": False, "quiet_zone": 0})
    barcode_img = Image.open(barcode_buffer)
    return barcode_img.resize((available_width, barcode_height), Image.NEAREST)


def render_text_with_wand(text, width_px, height_px, font_path, font_size, x, y):
    with WandImage(
        width=width_px, height=height_px, background=Color("transparent")
    ) as text_layer:
        with WandDrawing() as draw:
            draw.font = font_path
            draw.font_size = font_size
            draw.fill_color = Color("black")
            draw.resolution = (300, 300)

            draw.text(x, y, text)

            draw(text_layer)

        text_layer.format = "png"
        text_layer.alpha_channel = "activate"
        text_buffer = BytesIO(text_layer.make_blob("png"))
        return Image.open(text_buffer).convert("RGBA")


def generate_barcode_image(barcode_text, label_width_mm, label_height_mm, domain):
    pixels_per_mm = 8
    width_px, height_px = calculate_dimensions(
        label_width_mm, label_height_mm, pixels_per_mm
    )

    padding_mm = {"top": 2.2, "right": 2.3, "left": 2}
    padding_px = calculate_padding(padding_mm, pixels_per_mm)

    available_width = width_px - padding_px["left"] - padding_px["right"]

    response = requests.get(
        f"https://{domain}/api/purchase-order?barcode={barcode_text}"
    )

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

    product_data = [
        f"Артикл:  {product_name}",
        f"Увозник:  {plt_company_name}",
        f"Седиште:  {plt_company_location}",
        f"Потекло: Кина   Origjina: Kina",
        f"Artikulli:  {product_name_alb}",
        f"Importues:  {plt_company_name_alb}",
        f"Shtabi:  {plt_company_location_alb}",
    ]

    # use local path
    path = os.path.dirname(os.path.abspath(__file__))
    font_path = f"{path}/fonts/HarmonyOS_Sans_Regular.ttf"

    image = Image.new("RGBA", (width_px, height_px), color=(255, 255, 255, 255))

    product_font_size = int(height_px * 0.080)
    text_height = product_font_size
    spacing = int(height_px * 0.01)
    current_y = padding_px["top"]

    for i, text in enumerate(product_data):
        text_pil = render_text_with_wand(
            text,
            width_px,
            height_px,
            font_path,
            product_font_size,
            padding_px["left"],
            current_y,
        )
        image.paste(text_pil, (0, 0), text_pil)
        current_y += text_height + spacing

    barcode_height = int(height_px * 0.30)
    barcode_img = generate_barcode(
        barcode_text, available_width, barcode_height, font_path
    )
    image.paste(barcode_img, (padding_px["left"], current_y - text_height))

    barcode_font_size = int(height_px * 0.09)

    barcode_text_y = current_y + barcode_height + spacing
    barcode_text_pil = render_text_with_wand(
        barcode_text,
        width_px,
        height_px,
        font_path,
        barcode_font_size,
        padding_px["left"] + 105,
        barcode_text_y,
    )
    image.paste(barcode_text_pil, (0, 0), barcode_text_pil)

    return image
