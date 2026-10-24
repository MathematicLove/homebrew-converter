class AyzeksConverter < Formula
  desc "Convert HEIC, PDF, EPUB, Office, iWork and video files with one command"
  homepage "https://github.com/MathematicLove/homebrew-converter"
  url "https://github.com/MathematicLove/homebrew-converter/archive/refs/tags/v1.0.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"
  head "https://github.com/MathematicLove/homebrew-converter.git", branch: "main"

  depends_on :macos
  depends_on "python@3.13"

  conflicts_with "imagemagick", because: "both install a `convert` executable"

  def install
    bin.install "bin/convert"
    libexec.install "libexec/convert_core.py"
    zsh_completion.install "completions/_convert"
  end

  def caveats
    <<~EOS
      Python dependencies (PyMuPDF, pdf2docx) are installed on first use into
        ~/.local/share/macos-converter/venv
      To install them right away, run:
        convert --setup

      zsh ships an ImageMagick completion for `convert`. To get the converter
      completion on Tab, add this line to ~/.zshrc after compinit:
        compdef _convert convert
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/convert --version")
    assert_match "pdf-png", shell_output("#{bin}/convert --help")
  end
end
