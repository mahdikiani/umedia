"""MIME → local static icon filenames.

Icons live in `app/statics/` and are served by this process at
`/api/v1/statics/{filename}`. Do not point at an external host.
"""

from server.config import Settings

UNKNOWN_ICON = "unknown_flat_ico.svg"

mime_type_to_icon = {
    "application/msword": "text_color_doc.svg",
    "application/vnd.ms-excel": "text_color_xls.svg",
    "application/vnd.ms-powerpoint": "text_color_ppt.svg",
    "application/pdf": "text_color_pdf.svg",
    "application/zip": "text_color_zip.svg",
    "application/x-rar-compressed": "text_color_rar.svg",
    "application/x-msdownload": "text_color_exe.svg",
    "application/json": "text_color_json.svg",
    "application/javascript": "text_color_js.svg",
    "text/css": "text_color_css.svg",
    "text/csv": "text_color_csv.svg",
    "text/html": "text_color_html.svg",
    "text/x-java-source": "text_color_java.svg",
    "image/jpeg": "text_color_jpeg.svg",
    "image/png": "text_color_png.svg",
    "image/gif": "text_color_gif.svg",
    "image/svg+xml": "text_color_svg.svg",
    "image/webp": "text_color_webp.svg",
    "image/vnd.adobe.photoshop": "text_color_psd.svg",
    "image/x-icon": "text_color_ico.svg",
    "image/tiff": "text_color_tiff.svg",
    "video/mp4": "text_color_mp4.svg",
    "video/mpeg": "text_color_mpg.svg",
    "video/quicktime": "text_color_mov.svg",
    "video/x-msvideo": "text_color_avi.svg",
    "audio/mpeg": "text_color_mp3.svg",
    "audio/wav": "text_color_wav.svg",
    "text/plain": "text_color_txt.svg",
    "application/vnd.sketch": "text_color_skt.svg",
    "application/postscript": "text_color_ai.svg",
    "application/vnd.cdr": "text_color_cdr.svg",
    "application/x-blender": "text_color_blend.svg",
    "application/x-cinema4d": "text_color_c4d.svg",
    "application/x-adobe-aftereffects": "text_color_aep.svg",
    "application/x-hdf5": "icon_color_h5.svg",
    "application/vnd.figma": "text_color_fig.svg",
    "application/x-bar": "icon_color_bar.svg",
    "application/x-pie": "icon_color_pie.svg",
    "application/x-sheet": "icon_color_sheet.svg",
    "application/x-file": "icon_color_file.svg",
    "application/x-page": "icon_color_page.svg",
    "application/x-code": "icon_color_code.svg",
    "application/x-fig": "icon_color_fig.svg",
    "audio/basic": "icon_color_audio.svg",
    "video/x-generic": "icon_color_video.svg",
    "image/x-generic": "icon_color_img.svg",
    "text/x-document": "icon_color_doc.svg",
    "application/x-unknown": UNKNOWN_ICON,
    "inode/directory": "folder-1485.svg",
}


def icon_url(filename: str) -> str:
    return f"{Settings.base_path}/statics/{filename}"


unknown_icon_url = icon_url(UNKNOWN_ICON)

mime_type_to_icon_url = {
    key: icon_url(value) for key, value in mime_type_to_icon.items()
}


def get_icon_from_mime_type(mime_type: str) -> str:
    return mime_type_to_icon_url.get(mime_type, unknown_icon_url)
