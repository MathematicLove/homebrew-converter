# zsh-macos-converter

One `convert` command for the file conversions you need every day on a Mac:
HEIC photos, PDF pages, e-books, Word, Pages, Numbers, Keynote, PowerPoint and video.

```sh
convert heic-jpg IMG_0042.HEIC
convert pdf-png report.pdf 3
convert word-pdf Contract.docx -d ~/Desktop
```

- Works from any folder. Results go to the current folder, or to the folder given with `-d`.
- Press **Tab** after `convert` to see all modes. After a mode, Tab suggests only matching files.
- Existing files are never overwritten unless you pass `-f`. A suffix is added instead: `photo_1.png`.
- Batch friendly: `convert heic-png *.heic` converts every file and prints a summary.
- Clear error messages: missing file, wrong extension, page count out of range, password protected PDF, missing app.

## Conversion modes

| Group | Mode | What it does |
|---|---|---|
| Images | `heic-png`, `heic-jpg` | iPhone HEIC photos to PNG or JPG |
| | `png-heic`, `jpg-heic` | PNG or JPG to HEIC (much smaller files) |
| | `png-jpg`, `jpg-png` | PNG to JPG (transparency becomes white) and back |
| | `webp-png`, `webp-jpg` | WebP images from the web to PNG or JPG |
| | `img-pdf` | Several images into one PDF, one image per A4 page |
| PDF | `pdf-png [N]`, `pdf-jpg [N]` | Pages to a folder of images, all pages or the first N |
| | `pdf-txt` | Plain text in reading order |
| | `pdf-word` | Editable DOCX with paragraphs, tables and images |
| | `pdf-epub` | Reflowable EPUB with chapters, headings and images |
| Books | `epub-pdf` | EPUB to PDF, keeps the book styles, the table of contents becomes PDF bookmarks |
| Documents | `word-pdf` | Word (doc, docx, rtf, odt) to PDF |
| | `word-pages` | Word to Pages |
| | `pages-pdf`, `pages-word` | Pages to PDF or DOCX |
| Spreadsheets | `numbers-xlsx`, `numbers-pdf` | Numbers to Excel or PDF |
| | `xlsx-numbers` | Excel or CSV to Numbers |
| Presentations | `key-pdf`, `key-pptx` | Keynote to PDF or PowerPoint |
| | `pptx-key`, `pptx-pdf` | PowerPoint to Keynote or PDF |
| Video | `mov-mp4` | MOV or M4V (screen recordings, iPhone videos) to MP4 |

## Install

Requirements: macOS 12 or newer and zsh (the default shell on macOS).

### Homebrew

```sh
brew tap MathematicLove/converter
brew trust MathematicLove/converter
brew install ayzeks-converter
```

`brew trust` is needed on Homebrew 7 and newer, which only loads formulae from third-party taps you trust.
On older Homebrew versions skip that line.

To get Tab completion, add this line to `~/.zshrc` after `compinit`:

```sh
compdef _convert convert
```

You need it because zsh ships its own ImageMagick completion for the name `convert`.

### One-line install script

```sh
curl -fsSL https://raw.githubusercontent.com/MathematicLove/homebrew-converter/main/install.sh | zsh
```

The script copies the tool to `~/.local/share/macos-converter`, links `convert` into `~/.local/bin`,
adds one `source` line to `~/.zshrc` (PATH and Tab completion) and installs the Python dependencies.
Open a new terminal window afterwards.

### Manual download

1. Download the ZIP from the green **Code** button on GitHub, or run
   `git clone https://github.com/MathematicLove/homebrew-converter.git`
2. In the unpacked folder run:

```sh
./install.sh
```

### First run

PyMuPDF and pdf2docx are installed automatically into a private virtual environment
(`~/.local/share/macos-converter/venv`) the first time you convert something. This takes about a minute.
You can do it ahead of time with `convert --setup`.

## Usage

```
convert <mode> <file> [file ...] [options]
convert pdf-png <file.pdf> [N] [options]
convert img-pdf <image> [image ...] [-o name] [options]
```

| Option | Meaning |
|---|---|
| `-d, --dir <folder>` | Where to save results. The folder is created if needed. Default: current folder |
| `-o, --output <name>` | Output name when there is a single result, for example `-o passport` |
| `-f, --force` | Overwrite existing files |
| `-h, --help` | Help with all modes and examples |
| `-v, --version` | Version |
| `--setup` | Reinstall the Python dependencies |

## Uninstall

Homebrew:

```sh
brew uninstall ayzeks-converter
rm -rf ~/.local/share/macos-converter
```

Install script:

```sh
curl -fsSL https://raw.githubusercontent.com/MathematicLove/homebrew-converter/main/install.sh | zsh -s -- --uninstall
```
