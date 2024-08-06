import logging
import re

import click
from PIL import Image, ImageDraw, ImageFont
import barcode
from io import BytesIO
import platform
import os
import subprocess
from wand.image import Image as WandImage
from wand.drawing import Drawing as WandDrawing
from wand.color import Color
import io
from barcode_gen import generate_barcode_image

from printer import TCPTransport, PrinterClient, SerialTransport


@click.command("print")
@click.option(
    "-m",
    "--model",
    type=click.Choice(["k3", "b1", "b18", "b21", "d11", "d110"], False),
    default="b21",
    show_default=True,
    help="Niimbot printer model",
)
@click.option(
    "-c",
    "--conn",
    type=click.Choice(["usb", "tcp"]),
    default="usb",
    show_default=True,
    help="Connection type",
)
@click.option(
    "-a",
    "--addr",
    help="TCP address OR serial device path",
)
@click.option(
    "-d",
    "--density",
    type=click.IntRange(1, 5),
    default=5,
    show_default=True,
    help="Print density",
)
@click.option(
    "-r",
    "--rotate",
    type=click.Choice(["0", "90", "180", "270"]),
    default="0",
    show_default=True,
    help="Image rotation (clockwise)",
)
@click.option(
    "-i",
    "--image",
    type=click.Path(exists=True),
    # required=True,
    help="Image path",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Enable verbose logging",
)

## add barcode input option, where i enter the exact barcode i want to print


@click.option(
    "-b",
    "--barcode",
    type=click.STRING,
    required=False,
    help="Barcode to print",
)


# add click option for count of copies
@click.option(
    "-n",
    "--copies",
    type=click.IntRange(1, 100),
    default=1,
    show_default=True,
    help="Number of copies to print",
)
@click.option(
    "-l",
    "--domain",
    type=click.STRING,
    # required=True,
    help="Domain to print",
)
def print_cmd(
    model, conn, addr, density, rotate, image, verbose, copies, barcode, domain
):
    logging.basicConfig(
        level="DEBUG" if verbose else "INFO",
        format="%(levelname)s | %(module)s:%(funcName)s:%(lineno)d - %(message)s",
    )

    if conn == "tcp":
        assert conn is not None, "--addr argument required for tcp connection"

        # TCP split address and port

        address = re.split(":", addr)

        # port integer

        port = int(address[1])

        transport = TCPTransport(port=port, host=address[0])
    if conn == "usb":
        port = addr if addr is not None else "auto"
        transport = SerialTransport(port=port)

    if model in ("k3", "b1", "b18", "b21"):
        max_width_px = 384
    if model in ("d11", "d110"):
        max_width_px = 96

    if model in ("b18", "d11", "d110") and density > 3:
        logging.warning(f"{model.upper()} only supports density up to 3")
        density = 3

    if barcode is not None:
        image = generate_barcode_image(barcode, 40, 20, domain)
        save_and_open_image(image)
        click.confirm("Contin/ue printing?", abort=True)
    else:
        image = Image.open(image)

    if rotate != "0":
        # PIL library rotates counter clockwise, so we need to multiply by -1
        image = image.rotate(-int(rotate), expand=True)
    assert image.width <= max_width_px, f"Image width too big for {model.upper()}"

    printer = PrinterClient(transport)

    status = printer.heartbeat()
    printer.print_image(image, density=density, copies=copies)


def save_and_open_image(image, filename="barcode_preview.jpg"):
    # Save the image as JPEG
    image.save(filename, "PNG")

    # Open the image with the default viewer
    if platform.system() == "Darwin":  # macOS
        subprocess.call(["open", filename])
    elif platform.system() == "Windows":
        os.startfile(filename)
    else:  # Linux and other OS
        subprocess.call(["xdg-open", filename])


if __name__ == "__main__":
    print_cmd()
