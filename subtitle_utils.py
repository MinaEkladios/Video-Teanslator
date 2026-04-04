"""
subtitle_utils.py — Shared subtitle and FFmpeg utilities.
Imported by both app.py (/burn route) and blueprints/api_v1.py (/api/v1/burn).
Keeping this isolated avoids circular imports.
"""
import os


def time_to_ass_format(seconds: float) -> str:
    """Convert seconds to H:MM:SS.cs (centiseconds) for ASS."""
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    cs = int((seconds * 100) % 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def get_ffmpeg_exec() -> str | None:
    import shutil
    # First: try system ffmpeg (Linux/Mac/production)
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return system_ffmpeg

    # Second: try local Windows build (development only)
    local = os.path.join(os.getcwd(),
                         'ffmpeg-8.0.1-full_build', 'bin', 'ffmpeg.exe')
    if os.path.exists(local):
        return local

    return None


def generate_ass_file(subtitles, style_config, video_width, video_height,
                      output_path, play_res_w=None, play_res_h=None):
    """
    Generate a styled SSA/ASS subtitle file from a list of segment dicts.

    play_res_w/h — canvas display dimensions used as PlayRes; libass
    automatically scales from these coordinates to the native video resolution,
    so the font size in CSS pixels equals what appears on screen.
    """
    if play_res_w is None:
        play_res_w = video_width
    if play_res_h is None:
        play_res_h = video_height

    fontsize = style_config.get('fontSize', 24)

    # ── Colour helpers ────────────────────────────────────────────────────────
    def hex_to_ass_color(hex_color):
        """#RRGGBB → &H00BBGGRR"""
        if not hex_color:
            return "&H00FFFFFF"
        h = hex_color.lstrip('#')
        if len(h) == 6:
            return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}"
        return "&H00FFFFFF"

    font_raw  = style_config.get('fontFamily', 'Arial')
    font_name = font_raw.split(',')[0].replace("'", "").replace('"', "").strip()

    primary_color = hex_to_ass_color(style_config.get('color', '#ffffff'))

    stroke_color_hex  = style_config.get('strokeColor', '#000000')
    stroke_width_val  = float(style_config.get('strokeWidth', 1.5))
    outline_color     = hex_to_ass_color(stroke_color_hex)

    # ── Background box (temporarily disabled — BorderStyle=3 unsupported on Windows libass) ──
    back_color   = "&H80000000"
    border_style = 1   # outline only
    outline_size = round(max(0.0, stroke_width_val), 1)
    shadow_size  = 0.5

    # ── Position / alignment ──────────────────────────────────────────────────
    alignment_map = {'bottom': 2, 'top': 8, 'center': 5, 'custom': 2}
    alignment = alignment_map.get(style_config.get('position', 'bottom'), 2)

    margin_v = 50 if style_config.get('position') in ('bottom', 'top') else 20

    # ── Arabic detection ──────────────────────────────────────────────────────
    is_arabic = False
    first_text = subtitles[0].get('text', '') if subtitles else ''
    if any(0x0600 <= ord(c) <= 0x06FF for c in first_text):
        is_arabic = True

    # Use encoding 178 for Arabic script; keep the user-chosen alignment (centered by default).
    # Arabic text renders RTL correctly via Unicode BiDi even with center alignment.
    encoding = 178 if is_arabic else 1

    # ── ASS header ───────────────────────────────────────────────────────────
    ass_content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_res_w}\n"
        f"PlayResY: {play_res_h}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{fontsize},{primary_color},"
        f"&H000000FF,{outline_color},{back_color},"
        f"0,0,0,0,100,100,0,0,{border_style},{outline_size},{shadow_size},"
        f"{alignment},10,10,{margin_v},{encoding}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    # ── Dialogue lines ────────────────────────────────────────────────────────
    pos = style_config.get('position', 'bottom')
    animation = style_config.get('animation', 'none')

    for sub in subtitles:
        start = time_to_ass_format(float(sub['start']))
        end   = time_to_ass_format(float(sub['end']))
        text  = sub.get('text', '').replace('\n', r'\N')

        anim_tags = ""
        if animation == 'fade':
            anim_tags = r"{\fad(500,500)}"
        elif animation == 'slide-up':
            x_pos = play_res_w / 2
            y_pos = (margin_v if pos == 'top'
                     else play_res_h / 2 if pos == 'center'
                     else play_res_h - margin_v)
            y_start = y_pos + 50
            anim_tags = (r"{\move(" +
                         f"{x_pos},{y_start},{x_pos},{y_pos},0,500" +
                         r")\fad(200,0)}")
        elif animation == 'scale':
            anim_tags = r"{\fscx50\fscy50\fad(100,0)\t(0,300,\fscx100\fscy100)}"

        ass_content += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{anim_tags}{text}\n"

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass_content)
