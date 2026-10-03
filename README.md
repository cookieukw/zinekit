# zinekit

[Português (Brasil)](README-pt-BR.md)

![zinekit](docs/images/text.jpg)

zinekit turns images, typed titles and whole folders of images and videos into a 1970s photocopied zine: ransom-note letters, scissor-cut stickers, xerox and riso halftone.

The look comes from **Punk Zine**, a frei0r filter written alongside zinekit. frei0r is the plugin format that video tools such as ffmpeg and MLT load, so the same filter can also run there. zinekit ships the filter's C source and compiles it on the first run; nothing else needs to be installed.

It has two parts:

- **The editor** (Qt): an image, a title or a batch on the left, a live preview in the middle and every parameter on the right. The preview re-renders while you drag a slider.
- **The command line:** the same prints, presets and batch, for scripts.

![The editor: a game frame printed as a two-color riso, before and after](docs/images/editor-image.png)

![The three modes](docs/images/modes.jpg)

## Contents

- [Install and run](#install-and-run)
- [The editor](#the-editor)
- [Batch: images and videos](#batch-images-and-videos)
- [Command line](#command-line)
- [Parameters](#parameters)
- [Presets](#presets)
- [Same look in ffmpeg](#same-look-in-ffmpeg)
- [How it works](#how-it-works)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

## Install and run

You need:

- Linux (macOS should work too).
- Python 3.9 or newer.
- A C compiler (`gcc` or `clang`). It is used once, to build the plugin.
- `ffmpeg` and `ffprobe`, for videos.

```sh
cd ~/Documents/tools/zinekit
./run.sh
```

On the first run, `run.sh` creates `.venv/` in this folder and installs **PySide6-Essentials** and **Pillow** (about 100 MB to download). zinekit then compiles the plugin into `~/.cache/zinekit/`, which takes a few seconds. Later runs start straight away.

| command | what it does |
|---|---|
| `./run.sh` | opens the editor |
| `./run.sh photo.jpg` | opens the editor with that image. Several files or a video go to the Batch tab. |
| `./run.sh apply …`, `./run.sh text …` | runs the [command line](#command-line) |
| `./run.sh --install-desktop` | adds zinekit to the application menu |
| `./run.sh --selftest` | runs every test, including the editor on Qt's offscreen platform |
| `./run.sh --update` | reinstalls or upgrades the Python packages |

Use another Python with `PYTHON=python3.12 ./run.sh`.

To install it like a normal package instead:

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[gui]"
.venv/bin/zinekit          # or zinekit-gui
```

## The editor

The left column picks what to print, the middle shows the live preview, and the right column holds the parameters and presets.

### Image tab

- **Open image…** (Ctrl+O), **Paste image** (Ctrl+Shift+V), or drag a file onto the window.
- **Save result…** (Ctrl+S) renders at full resolution and writes PNG, JPEG, WebP or TIFF. PNG, WebP and TIFF keep transparency. JPEG is flattened over the Batch tab's *Background* color.
- **Copy result** (Ctrl+Shift+C) puts the full-size print on the clipboard.

### Text tab

Type a title (several lines are fine) and pick:

- font (every installed font, searchable, or **…** to load a `.ttf`/`.otf`), size, color and outline;
- alignment, line and letter spacing;
- canvas: 1920×1080, 1280×720, 4K, square, 4:5, 9:16, or *Fit to text*; and margin.

The title is drawn on a transparent canvas, which the plugin's text mode needs to cut every letter onto its own scrap. **Save PNG…** keeps the transparency, so the file can go straight onto a track in a video editor.

An outline is knocked out of the scrap instead of being flattened, so outlined titles stay readable.

![The Text tab](docs/images/editor-text.png)

### Batch tab

Add files or folders, or drop them onto the window. Picking a file in the list previews it with the current parameters. For a video, the **Frame** slider scrubs through it.

Then choose an output folder, formats and size, and press **Start**. See [Batch: images and videos](#batch-images-and-videos).

![The Batch tab after a run: an image, a photo and a video printed with the Pink riso sticker preset](docs/images/editor-batch.png)

### Preview

| control | |
|---|---|
| **After / Before / Split** | Split shows the original on the left and the print on the right; drag on the picture to move the line |
| **Before** button, or hold **Space** | shows the original while held |
| background | checkerboard, black, gray or white behind transparent prints |
| **540p / 720p / 1080p / Full** | the resolution the preview is rendered at |

The preview is rendered on a background thread, and only the newest request is rendered. While you drag a slider, the picture keeps up instead of queueing up stale frames. The time each render took is shown on the right.

Every length in the effect (halftone cell, rough edges, shadow, scrap padding) scales with the frame size. A 720p preview therefore looks like the full-resolution render.

### Parameters panel

These are all of the filter's parameters, grouped into General, Text, Element, Image, Halftone, Colors and Layout. Each one has a tooltip that says what it does and which mode uses it.

- Parameters the selected **Print mode** does not use are greyed out (all of them are active in *Auto detect*).
- **Double-click a label** to reset that parameter.
- Sliders and lists only take the mouse wheel after you click them, so scrolling the panel never changes a value by accident.

### Shortcuts

| keys | |
|---|---|
| Ctrl+O | open image |
| Ctrl+Shift+V | paste image |
| Ctrl+S | save result |
| Ctrl+Shift+C | copy result |
| Ctrl+1 / Ctrl+2 / Ctrl+3 | Image / Text / Batch tab |
| Space (hold) | show the original |
| Ctrl+Q | quit |

### Language

The interface is in English or Brazilian Portuguese. Switch with the language box at the right end of the menu bar, or **View → Language**. The window is rebuilt in the new language right away, keeping the open image, the batch list and every setting. The first start follows the system language; `ZINEKIT_LANG=pt_BR` or `ZINEKIT_LANG=en` overrides it. The command line is always in English.

No text is hardcoded in the interface. Every text comes from `zinekit/locales/<code>.json`, a flat JSON file that maps the English text to the translation, with `"_language"` as the name shown in the menu. To add a language, copy `en.json` to e.g. `es.json`, translate the values and restart; it appears in the menu by itself. `tests/test_i18n.py` fails when a file is missing a text, keeps one the code no longer uses, or changes a `%s`/`%d` placeholder.

![The editor in Portuguese](docs/images/editor-pt-BR.png)

The editor remembers the parameters, the preset, the text and the batch settings between sessions, in `~/.config/zinekit/zinekit.conf`.

## Batch: images and videos

Inputs:

- **Images:** png, jpg, webp, bmp, tif, tga. The EXIF rotation is applied.
- **Videos:** anything ffmpeg reads (mp4, mov, mkv, webm, avi, gif, …). Phone rotation is applied, and VP9/ProRes/PNG transparency is kept.

Image outputs:

| format | transparency |
|---|---|
| PNG | ✓ |
| JPEG | flattened over *Background* |
| WebP | ✓ |
| TIFF | ✓ |
| Same as source | as the source format |

Video outputs:

| format | transparency | audio | notes |
|---|---|---|---|
| MP4 H.264 | flattened over *Background* | AAC, copied from the source | plays everywhere |
| MOV ProRes 4444 | ✓ | PCM | for editing; video editors read the alpha |
| WebM VP9 | ✓ | Opus | small, with alpha |
| PNG sequence | ✓ | none | a folder of `frame_000001.png` |
| GIF | ✓ (1-bit) | none | palette made from the clip |

Options:

- **Max height** downscales (never upscales). Video sizes are kept even for H.264.
- **Background** is the color under transparent areas for formats that have no alpha: black, white or the paper color.
- **Suffix** is added to the file name (`clip.mp4` → `clip_zine.mp4`). An existing file is never replaced unless **Overwrite** is on; the new file becomes `clip_zine-2.mp4` instead.
- **Same folder as each file** writes next to the inputs.

Behaviour:

- A file that fails is reported in the log, and the batch goes on with the next one.
- Image and video files are written as a hidden `.name.part…` file and only renamed when complete. **Cancel** stops ffmpeg and removes that partial file; frames of a cancelled PNG sequence stay in their folder.
- How fast a video goes depends on the resolution and the CPU. On a 2-core machine, a 720p frame takes about 80 ms. The plugin uses up to 8 threads per frame, and ffmpeg decodes and encodes alongside it.

## Command line

`zinekit` with no command opens the editor. Run it as `./run.sh …`, as `.venv/bin/zinekit …` after a pip install, or as `python3 -m zinekit …`.

```sh
# one image, one output file (the extension picks the format)
zinekit apply photo.jpg -o photo_zine.png -p "Xerox photo" -s dot_size=40

# a folder of images and videos into another folder
zinekit apply ~/Videos/clips -o ~/Videos/zine --video-format mov --max-height 1080 -r

# a title as ransom-note letters on a transparent 1080p PNG
zinekit text "NO FUTURE\nzine #1" -o title.png --font "DejaVu Sans Bold" --size 200 --stroke 6

# the title layer only, without the effect
zinekit text "NO FUTURE" -o plain.png --plain

zinekit params              # every parameter, its default, values and where it applies
zinekit presets             # the presets; 'zinekit presets "Riso duotone"' prints one as JSON
zinekit fonts bold          # installed fonts matching "bold"
zinekit ffmpeg -p "Blue riso"   # the same look as an ffmpeg filter
zinekit doctor              # compiler, plugin, ffmpeg encoders, PySide6
zinekit build --force       # recompile the plugin
```

`-s/--set NAME=VALUE` takes values the way the editor shows them:

| kind | example |
|---|---|
| percent | `-s roughness=80` (or `80%`) |
| degrees | `-s dot_angle=30` |
| list items, by name | `-s mode=text`, `-s image_style=riso2`, `-s scrap_palette=bw` |
| on/off | `-s keep_text_color=on` |
| colors | `-s ink=#000000` |
| seed (0–1000) | `-s seed=42` |

`-p/--preset` takes a preset name or a `.json` file. The preset is applied first, then each `-s`.

`zinekit apply` exits with 0 when every file worked and 1 when any failed. Bad arguments exit with 2.

## Parameters

| name | panel | default | values | applies to |
|---|---|---|---|---|
| `mode` | Print mode | Auto detect | auto, text, element, image | all |
| `mix` | Effect amount | 100% | 0–100% | all |
| `roughness` | Rough edges | 50% | 0–100% | all |
| `paper_texture` | Paper texture | 60% | 0–100% | all |
| `grain` | Toner grain | 40% | 0–100% | all |
| `misregistration` | Misregistration | 40% | 0–100% | all (image: riso styles) |
| `shadow` | Shadow distance | 50% | 0–100% | text, element |
| `shadow_opacity` | Shadow opacity | 50% | 0–100% | text, element |
| `scrap_palette` | Text → Scraps | Mixed | mixed, bw, plate, plate2, none | text |
| `chaos` | Text → Chaos | 60% | 0–100% | text |
| `scrap_padding` | Text → Padding | 50% | 0–100% | text |
| `keep_text_color` | Text → Keep color | off | on, off | text |
| `element_style` | Element → Style | Auto detect | auto, riso, palette, xerox, original | element |
| `cut_margin` | Element → Margin | 50% | 0–100% | element |
| `outline` | Element → Outline | 30% | 0–100% | element |
| `recolor` | Element → Recolor | 100% | 0–100% | element |
| `image_style` | Image → Style | Xerox | xerox, riso, riso2, photocopy | image |
| `burn` | Image → Burn | 35% | 0–100% | image |
| `dot_size` | Halftone → Dot size | 22% | 0–100% | element, image |
| `dot_angle` | Halftone → Dot angle | 45° | 0–45° | element, image |
| `contrast` | Halftone → Xerox contrast | 55% | 0–100% | element, image |
| `ink` | Ink | `#151311` | color | all |
| `paper` | Paper | `#f7f3e8` | color | all |
| `color1` | Plate color | `#ff4fa8` | color | all |
| `color2` | Plate color 2 | `#35d45b` | color | all |
| `color3` | Scrap color | `#ffe24a` | color | text, element |
| `seed` | Layout seed | 1 | 0–1000 | all |

*Auto detect* chooses per picture:

- many separate pieces on transparency: **text**;
- one piece on transparency: **element**;
- no transparency: **image**.

The algorithms behind each mode are described in the plugin's README, in the frei0r fork (`src/filter/punkzine/README.md` on the `filter/punkzine` branch).

## Presets

Built-in presets:

- Default
- Ransom note
- Ransom note, black and white
- Pink riso sticker
- Clean cut-out
- Xerox photo
- Riso duotone
- Burned photocopy
- Blue riso

![Presets](docs/images/presets.jpg)

In the editor:

- **Save preset…** stores the current values as your own preset.
- The **⋯** menu deletes, imports and exports presets, resets everything, or copies the ffmpeg filter.
- *(modified)* appears when the panel no longer matches the selected preset.

User presets are JSON files in `~/.config/zinekit/presets/` (`$XDG_CONFIG_HOME` is honoured; `ZINEKIT_CONFIG` moves the whole folder). A preset only lists the values that differ from the defaults, in plugin units: doubles 0–1, lists as evenly spaced values, colors as hex.

```json
{
  "zinekit": 1,
  "name": "Riso duotone",
  "params": {"mode": 1.0, "misregistration": 0.5, "image_style": 0.666667, "dot_size": 0.3}
}
```

## Same look in ffmpeg

`zinekit ffmpeg` (or **⋯ → Copy ffmpeg filter**) prints the frei0r filter string with every parameter in order.

```sh
FREI0R_PATH=~/.cache/zinekit/frei0r-1 ffmpeg -i in.mp4 -vf "format=rgba,$(zinekit ffmpeg -p 'Riso duotone')" out.mp4
```

`~/.cache/zinekit/frei0r-1/punkzine.so` always links to the current build (`zinekit build` prints the path). With the same input pixels, ffmpeg's output and zinekit's differ by at most 1/255 per channel. Other frei0r hosts (MLT, for instance) can load the same module from that folder.

## How it works

```
zinekit/
├── native/punkzine.c   the frei0r plugin (vendored, see native/README.md)
├── engine.py           compiles it once into ~/.cache/zinekit, loads it with ctypes
├── params.py           the 27 parameters: labels, groups, units, CLI parsing
├── presets.py          built-in and user presets (JSON)
├── textlayer.py        renders a title on a transparent canvas (Pillow)
├── fonts.py            installed fonts (fc-list, or a scan of the font folders)
├── media.py            ffprobe / ffmpeg: video info, single frames, encoders
├── batch.py            images with Pillow, videos through ffmpeg pipes
├── preview.py          the background worker that always renders the newest request
├── cli.py              the command line
├── i18n.py             tr(): looks texts up in locales/
├── locales/            en.json, pt_BR.json (one file per language)
└── gui/                the PySide6 editor
```

- **Engine.** `native/punkzine.c` is compiled with `cc -O3 -fPIC -shared` into `~/.cache/zinekit/punkzine-<hash>.so`. It is rebuilt only when the source changes. The module is loaded the way a frei0r host loads it: `f0r_construct`, `f0r_set_param_value`, `f0r_update`. ctypes releases the GIL during `f0r_update`, so renders run in parallel with the interface. Without a compiler, zinekit uses a `punkzine.so` already in one of the usual frei0r folders (`~/.frei0r-1/lib`, `/usr/lib/frei0r-1`, …) or the file named by `ZINEKIT_PLUGIN`.
- **Video.** One ffmpeg decodes the input to raw RGBA frames: constant frame rate, scaled, rotation applied. The plugin prints each frame. A second ffmpeg encodes the frames and copies the audio track of the original. Reading, printing and writing run on three threads.
- **Preview.** The source is downscaled to the preview size once. Each parameter change submits a job to one worker thread; jobs that are still waiting are replaced by the newest one. The plugin instance is kept per frame size, so the element mode reuses its cut-out between renders.

## Development

```sh
python3 -m unittest discover -s tests -t .       # all tests (needs Pillow; ffmpeg for the video tests)
./run.sh --selftest                              # the same inside the venv, editor included
python3 docs/make_examples.py                    # rebuild the example prints in docs/images
QT_QPA_PLATFORM=offscreen .venv/bin/python docs/make_screenshots.py   # retake the editor screenshots
```

- **Tests** cover the engine against the plugin's parameter table and the modes, parameters and presets, the text layer, image batches and every video format (alpha, audio, scaling, cancel), the preview worker, and the CLI.
- **`tests/test_gui_smoke.py`** drives the editor window end to end. With PySide6 installed it uses the real widgets on Qt's offscreen platform. Without it, it runs on `tests/fakeqt/`, a stand-in that executes the editor's own logic but checks no Qt API.
- **Updating the plugin:** copy `src/filter/punkzine/punkzine.c` from the frei0r fork into `zinekit/native/`. If parameters changed, also update `params.py` (`PARAMS`, `PLUGIN_ORDER`). `tests/test_engine.py` fails when the schema and the plugin disagree.

## Troubleshooting

- **"no C compiler found":** install one (`sudo pacman -S gcc`, `sudo apt install build-essential`, `sudo dnf install gcc`). If you already have a build of the filter, `ZINEKIT_PLUGIN=/path/to/punkzine.so` uses it instead.
- **The editor does not open, and Qt mentions "xcb":** PySide6 6.5+ needs `libxcb-cursor0` on X11 (`sudo apt install libxcb-cursor0`). On Wayland you can also try `QT_QPA_PLATFORM=wayland ./run.sh`.
- **pip cannot find PySide6:** your Python may be too new for the PySide6 wheels. Try `PYTHON=python3.12 ./run.sh --update`.
- **Videos are skipped:** install ffmpeg. Then run `./run.sh doctor` to see the encoders found. Without libx264, MP4 falls back to OpenH264 or MPEG-4.
- **Everything looks the same in the preview:** check **Effect amount**, and that the parameter is not greyed out for the selected **Print mode**.

## License

MIT, see [LICENSE](LICENSE). The Punk Zine plugin in `zinekit/native/` is MIT as well.
